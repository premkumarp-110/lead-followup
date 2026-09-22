import { useCallback, useEffect, useState } from 'react'
import api from '../services/api.js'
import CallAnalyzer from './CallAnalyzer.jsx'
import SummaryCards from './SummaryCards.jsx'
import FilterBar from './FilterBar.jsx'
import LeadTable from './LeadTable.jsx'
import LeadDetailsModal from './LeadDetailsModal.jsx'
import FollowUpActionModal from './FollowUpActionModal.jsx'

const EMPTY_FILTERS = {
  search: '', bd: '', course: '', outcome: '', status: '',
  date: '', date_from: '', date_to: '', bucket: 'ALL',
}

// Quick filters that describe closed leads live on the closed tab.
const CLOSED_BUCKETS = new Set(['CONVERTED', 'DROPPED'])

export default function Dashboard() {
  const [config, setConfig] = useState(null)
  const [filters, setFilters] = useState(EMPTY_FILTERS)
  const [options, setOptions] = useState({})
  const [summary, setSummary] = useState(null)
  const [followUps, setFollowUps] = useState([])
  const [closed, setClosed] = useState([])
  const [tab, setTab] = useState('follow-up')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const [detailLeadId, setDetailLeadId] = useState(null)
  const [detail, setDetail] = useState(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState('')
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

  useEffect(() => { load() }, [load])

  useEffect(() => {
    api.getConfig().then(setConfig).catch(() => setConfig({ audio_playback_enabled: true }))
    api.getFilterOptions().then(setOptions).catch(() => {})
  }, [])

  function selectBucket(bucket) {
    setFilters((f) => ({ ...f, bucket }))
    setTab(CLOSED_BUCKETS.has(bucket) ? 'closed' : 'follow-up')
  }

  async function openLead(leadId) {
    setDetailLeadId(leadId)
    setDetail(null)
    setDetailError('')
    setDetailLoading(true)
    try {
      setDetail(await api.getLead(leadId))
    } catch (err) {
      setDetailError(err.message)
    } finally {
      setDetailLoading(false)
    }
  }

  async function submitAction(payload) {
    await api.updateFollowUp(pendingAction.lead.lead_id, payload)
    const verb = { COMPLETE: 'marked done', CANCEL: 'cancelled', RESCHEDULE: 'rescheduled' }[payload.action]
    setNotice(`Follow-up for ${pendingAction.lead.name} ${verb}.`)
    setPendingAction(null)
    await load()
    if (detailLeadId === pendingAction.lead.lead_id) await openLead(detailLeadId)
  }

  function onCallAnalyzed(result) {
    const outcome = (result.outcome || '').replaceAll('_', ' ').toLowerCase()
    setNotice(`Call ${result.call_id} analyzed — outcome: ${outcome}. Dashboard refreshed.`)
    load()
  }

  const activeLeads = tab === 'follow-up' ? followUps : closed

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>Lead Follow-up Management</h1>
          <p>After every call, the conversation is analyzed and the lead's next action is decided.</p>
        </div>
        <button className="btn" onClick={load} disabled={loading}>
          {loading ? 'Refreshing…' : 'Refresh'}
        </button>
      </header>

      {config?.call_analyzer_enabled && (
        <CallAnalyzer config={config} onCompleted={onCallAnalyzed} onOpenLead={openLead} />
      )}

      {error && <div className="banner error">{error}</div>}
      {notice && (
        <div className="banner info" onClick={() => setNotice('')} role="status">{notice}</div>
      )}

      <SummaryCards summary={summary} activeBucket={filters.bucket} onSelectBucket={selectBucket} />

      <FilterBar
        filters={filters}
        options={options}
        onChange={(next) => {
          setFilters(next)
          if (next.bucket !== filters.bucket) setTab(CLOSED_BUCKETS.has(next.bucket) ? 'closed' : 'follow-up')
        }}
        onReset={() => { setFilters(EMPTY_FILTERS); setTab('follow-up') }}
      />

      <div className="tabs">
        <button className={`tab ${tab === 'follow-up' ? 'active' : ''}`} onClick={() => setTab('follow-up')}>
          Leads Requiring Follow-up ({followUps.length})
        </button>
        <button className={`tab ${tab === 'closed' ? 'active' : ''}`} onClick={() => setTab('closed')}>
          Completed / Closed ({closed.length})
        </button>
      </div>

      <LeadTable
        variant={tab}
        leads={activeLeads}
        loading={loading}
        onSelectLead={openLead}
        onAction={(lead, action) => setPendingAction({ lead, action })}
      />

      {detailLeadId && (
        <LeadDetailsModal
          lead={detail}
          loading={detailLoading}
          error={detailError}
          config={config}
          onClose={() => { setDetailLeadId(null); setDetail(null) }}
        />
      )}

      {pendingAction && (
        <FollowUpActionModal
          lead={pendingAction.lead}
          action={pendingAction.action}
          onClose={() => setPendingAction(null)}
          onSubmit={submitAction}
        />
      )}
    </div>
  )
}
