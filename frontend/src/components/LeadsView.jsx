import { useCallback, useEffect, useState } from 'react'
import api from '../services/api.js'
import { csvName, downloadCsv } from '../services/csv.js'
import FilterBar from './FilterBar.jsx'
import FollowUpActionModal from './FollowUpActionModal.jsx'
import LeadTable from './LeadTable.jsx'
import SummaryCards from './SummaryCards.jsx'
import { formatDateTime, outcomeLabel } from './format.js'

export const EMPTY_FILTERS = {
  search: '', bd: '', product: '', stage: '', language: '',
  lead_source: '', state: '', last_disposition_status: '', sentiment: '',
  connected_min: '', win_probability_min: '',
  outcome: '', status: '', date: '', date_from: '', date_to: '', bucket: 'ALL',
}

// Quick filters that describe closed leads live on the closed tab.
const CLOSED_BUCKETS = new Set(['CONVERTED', 'DROPPED'])

// Mirrors what the CRM actually carries. There is no name/phone/email upstream,
// so the export is keyed on the two ids instead.
const CSV_COLUMNS = [
  { key: 'lead_id', label: 'Lead ID' },
  { key: 'external_id', label: 'External ID' },
  { key: 'stage', label: 'Stage' },
  { key: 'product', label: 'Product' },
  { key: 'language', label: 'Language' },
  { key: 'lead_source', label: 'Lead Source' },
  { key: 'state', label: 'State' },
  { key: 'segmentation', label: 'Segmentation' },
  { key: 'win_probability', label: 'Win Probability' },
  { key: 'last_disposition_status', label: 'Last Disposition' },
  { key: 'total_attempts', label: 'Total Attempts' },
  { key: 'calls_connected', label: 'Calls Connected' },
  { key: 'total_talktime_sec', label: 'Talktime (s)' },
  { label: 'Sentiment', get: (l) => l.latest_sentiment?.label || '' },
  { label: 'Sentiment Score', get: (l) => l.latest_sentiment?.score ?? '' },
  { label: 'Sentiment Trend', get: (l) => l.latest_sentiment?.trajectory || '' },
  { label: 'BD', get: (l) => l.owner_name || 'Unassigned' },
  { label: 'BD Email', get: (l) => l.owner_email || '' },
  { key: 'lead_status', label: 'Lead Status' },
  { label: 'Outcome', get: (l) => outcomeLabel(l.latest_outcome) },
  { label: 'Follow-up Bucket', get: (l) => l.follow_up?.bucket || '' },
  { label: 'Follow-up Due', get: (l) => (l.follow_up?.datetime ? formatDateTime(l.follow_up.datetime) : '') },
  { label: 'Follow-up Reason', get: (l) => l.follow_up?.reason || '' },
  { label: 'Last Call', get: (l) => (l.last_call_at ? formatDateTime(l.last_call_at) : '') },
  { label: 'Updated', get: (l) => formatDateTime(l.updated_at) },
]

/**
 * The full lead table: filters, both tabs, summary, and follow-up actions.
 *
 * The shell owns the lead-detail modal and the BD selector, so this view only
 * reports which lead to open. The analyzer now has its own nav item and is no
 * longer wedged above the table.
 */
export default function LeadsView({ dataVersion, onOpenLead, onChanged }) {
  const [filters, setFilters] = useState(EMPTY_FILTERS)
  const [options, setOptions] = useState({})
  const [summary, setSummary] = useState(null)
  const [followUps, setFollowUps] = useState([])
  const [closed, setClosed] = useState([])
  const [tab, setTab] = useState('follow-up')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [pendingAction, setPendingAction] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const closedFilter = CLOSED_BUCKETS.has(filters.bucket)
      // Bucket filters are worklist concepts; the closed list only honours the
      // CONVERTED / DROPPED ones and ignores the rest so switching tabs never
      // shows an empty table by accident.
      const { bucket, ...shared } = filters
      const [summaryData, followUpData, closedData] = await Promise.all([
        api.getSummary(filters),
        api.getFollowUps(closedFilter ? shared : filters),
        api.getClosed(closedFilter ? filters : shared),
      ])
      setSummary(summaryData)
      setFollowUps(followUpData)
      setClosed(closedData)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [filters])

  useEffect(() => { load() }, [load, dataVersion])
  useEffect(() => { api.getFilterOptions().then(setOptions).catch(() => {}) }, [])

  function applyFilters(next) {
    setFilters(next)
    if (next.bucket !== filters.bucket) setTab(CLOSED_BUCKETS.has(next.bucket) ? 'closed' : 'follow-up')
  }

  function selectBucket(bucket) {
    setFilters((f) => ({ ...f, bucket }))
    setTab(CLOSED_BUCKETS.has(bucket) ? 'closed' : 'follow-up')
  }

  function resetFilters() {
    setFilters(EMPTY_FILTERS)
    setTab('follow-up')
  }

  async function submitAction(payload) {
    const lead = pendingAction.lead
    await api.updateFollowUp(lead.lead_id, payload)
    const verb = { RESCHEDULE: 'rescheduled' }[payload.action] || 'updated'
    setNotice(`Follow-up for ${lead.lead_id} ${verb}.`)
    setPendingAction(null)
    onChanged?.()
    await load()
  }

  const rows = tab === 'follow-up' ? followUps : closed
  const hasFilters = Object.entries(filters).some(([k, v]) => (k === 'bucket' ? v !== 'ALL' : Boolean(v)))

  function exportCsv() {
    downloadCsv(csvName('leads', rows.length, tab === 'follow-up' ? 'worklist' : 'closed'), rows, CSV_COLUMNS)
  }

  return (
    <>
      {error && <div className="banner error">{error}</div>}
      {notice && (
        <div className="banner success with-action" role="status">
          <span>{notice}</span>
          <button className="btn small subtle" onClick={() => setNotice('')}>Dismiss</button>
        </div>
      )}

      <SummaryCards
        summary={summary}
        loading={loading}
        activeBucket={filters.bucket}
        onSelectBucket={selectBucket}
      />

      <FilterBar
        filters={filters}
        options={options}
        resultCount={loading ? null : rows.length}
        onChange={applyFilters}
        onReset={resetFilters}
      />

      <div className="tabs">
        <button className={`tab ${tab === 'follow-up' ? 'active' : ''}`} onClick={() => setTab('follow-up')}>
          Requiring Follow-up ({followUps.length})
        </button>
        <button className={`tab ${tab === 'closed' ? 'active' : ''}`} onClick={() => setTab('closed')}>
          Completed / Closed ({closed.length})
        </button>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 6, alignItems: 'center' }}>
          <button className="btn small subtle" onClick={load} disabled={loading}>
            {loading ? 'Refreshing…' : 'Refresh'}
          </button>
          <button
            className="btn small"
            onClick={exportCsv}
            disabled={!rows.length}
            title={rows.length ? `Export these ${rows.length} rows` : 'Nothing to export'}
          >
            Export CSV
          </button>
        </div>
      </div>

      <LeadTable
        variant={tab}
        leads={rows}
        loading={loading}
        hasFilters={hasFilters}
        onSelectLead={onOpenLead}
        onAction={(lead, action) => setPendingAction({ lead, action })}
        onClearFilters={resetFilters}
      />

      {pendingAction && (
        <FollowUpActionModal
          lead={pendingAction.lead}
          action={pendingAction.action}
          onClose={() => setPendingAction(null)}
          onSubmit={submitAction}
        />
      )}
    </>
  )
}
