import { useCallback, useEffect, useState } from 'react'
import api from '../services/api.js'
import SummaryCards from './SummaryCards.jsx'
import FilterBar from './FilterBar.jsx'
import LeadTable from './LeadTable.jsx'
import LeadDetailsModal from './LeadDetailsModal.jsx'
import FollowUpActionModal from './FollowUpActionModal.jsx'

const EMPTY_FILTERS = {
  search: '', bd: '', course: '', outcome: '', status: '',
  date: '', date_from: '', date_to: '', bucket: 'ALL',
}

export default function Dashboard() {
  const [filters, setFilters] = useState(EMPTY_FILTERS)
  const [options, setOptions] = useState({})
  const [summary, setSummary] = useState(null)
  const [followUps, setFollowUps] = useState([])
  const [nonFollowUps, setNonFollowUps] = useState([])
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
      // The bucket filter only makes sense for the active worklist; the closed
      // list ignores it so switching tabs never shows an empty table by accident.
      const { bucket, ...shared } = filters
      const [summaryData, followUpData, nonFollowUpData] = await Promise.all([
        api.getSummary(filters),
        api.getFollowUps(filters),
        api.getNonFollowUps(shared),
      ])
      setSummary(summaryData)
      setFollowUps(followUpData)
      setNonFollowUps(nonFollowUpData)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [filters])

  useEffect(() => { load() }, [load])

  useEffect(() => {
    api.getFilterOptions().then(setOptions).catch(() => {})
  }, [])

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

  const activeLeads = tab === 'follow-up' ? followUps : nonFollowUps

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>Lead Follow-up Dashboard</h1>
          <p>Which leads need to be contacted, based on the latest call with each lead.</p>
        </div>
        <button className="btn" onClick={load} disabled={loading}>
          {loading ? 'Refreshing…' : 'Refresh'}
        </button>
      </header>

      {error && <div className="banner error">{error}</div>}
      {notice && (
        <div className="banner info" onClick={() => setNotice('')} role="status">{notice}</div>
      )}

      <SummaryCards
        summary={summary}
        activeBucket={filters.bucket}
        onSelectBucket={(bucket) => { setFilters((f) => ({ ...f, bucket })); setTab('follow-up') }}
      />

      <FilterBar
        filters={filters}
        options={options}
        onChange={setFilters}
        onReset={() => setFilters(EMPTY_FILTERS)}
      />

      <div className="tabs">
        <button
          className={`tab ${tab === 'follow-up' ? 'active' : ''}`}
          onClick={() => setTab('follow-up')}
        >
          Follow-ups Required ({followUps.length})
        </button>
        <button
          className={`tab ${tab === 'non-follow-up' ? 'active' : ''}`}
          onClick={() => setTab('non-follow-up')}
        >
          No Follow-up Needed ({nonFollowUps.length})
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
