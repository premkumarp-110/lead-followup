import { useCallback, useEffect, useState } from 'react'
import api from '../services/api.js'
import { readStored, writeStored } from '../services/storage.js'
import BDSelector from './BDSelector.jsx'
import InsightsView from './InsightsView.jsx'
import LeadDetailsModal from './LeadDetailsModal.jsx'
import LeadsView from './LeadsView.jsx'
import ReminderBell from './ReminderBell.jsx'
import WorklistView from './WorklistView.jsx'

const BD_KEY = 'lfm.bd'

/**
 * Application shell: sidebar, top bar, and the active view.
 *
 * View switching is local state rather than a router. Three views do not
 * justify a routing dependency, and nothing here needs deep links yet.
 *
 * The shell owns the two things every view shares -- which BD we are acting
 * as, and the lead-detail modal -- so any view can open a lead without
 * duplicating that state.
 */
export default function AppShell() {
  const [view, setView] = useState('worklist')
  const [config, setConfig] = useState(null)
  const [callers, setCallers] = useState([])
  const [bdId, setBdId] = useState(() => readStored(BD_KEY, '') || null)

  const [detailLeadId, setDetailLeadId] = useState(null)
  const [detail, setDetail] = useState(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState('')

  // Bumped whenever something changes a lead, so views refetch.
  const [dataVersion, setDataVersion] = useState(0)
  const refresh = useCallback(() => setDataVersion((v) => v + 1), [])

  useEffect(() => {
    api.getConfig().then(setConfig).catch(() => setConfig({ audio_playback_enabled: true }))
    // Inactive BDs included: they can still own pending follow-ups.
    api.getCallers(true).then(setCallers).catch(() => setCallers([]))
  }, [])

  // A BD who no longer exists would silently filter everything to nothing.
  useEffect(() => {
    if (!bdId || !callers.length) return
    if (!callers.some((c) => c.caller_id === bdId)) selectBd(null)
  }, [callers]) // eslint-disable-line react-hooks/exhaustive-deps

  function selectBd(next) {
    setBdId(next)
    writeStored(BD_KEY, next)
  }

  const openLead = useCallback(async (leadId) => {
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
  }, [])

  const activeBd = callers.find((c) => c.caller_id === bdId)

  // There is no "Analyze Call" nav item: recordings and transcripts already
  // exist on the call, so analysis is an action on a call, not a place to go.
  const NAV = [
    { key: 'worklist', label: 'My Worklist', icon: <ListIcon /> },
    { key: 'leads', label: 'Leads', icon: <TableIcon /> },
    { key: 'insights', label: 'Insights', icon: <ChartIcon /> },
  ]

  const TITLES = {
    worklist: ['My Worklist', activeBd ? `Acting as ${activeBd.name}` : 'All BDs'],
    leads: ['Leads', 'Every lead, filtered'],
    insights: ['Insights', 'Queue health and workload'],
  }
  // A view persisted before a nav change (e.g. 'system') falls back here.
  const safeView = TITLES[view] ? view : 'worklist'
  const [title, subtitle] = TITLES[safeView]

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="sidebar-inner">
        <div className="sidebar-brand">
          <span className="mark">LF</span>
          <span>Lead Follow-up</span>
        </div>
        <nav className="sidebar-nav">
          {NAV.map((item) => (
            <button
              key={item.key}
              type="button"
              className={`nav-item ${view === item.key ? 'active' : ''}`}
              onClick={() => setView(item.key)}
              aria-current={view === item.key ? 'page' : undefined}
            >
              <span className="nav-icon">{item.icon}</span>
              {item.label}
            </button>
          ))}
        </nav>
        <div className="sidebar-foot">
          {callers.length ? `${callers.length} BDs` : ''}
        </div>
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <h1>{title}</h1>
          <span className="topbar-sub">{subtitle}</span>
          <div className="topbar-right">
            <BDSelector callers={callers} value={bdId} onChange={selectBd} />
            <ReminderBell
              bdId={bdId}
              config={config}
              dataVersion={dataVersion}
              onOpenLead={openLead}
              onSent={refresh}
            />
          </div>
        </header>

        <main className="view">
          {safeView === 'worklist' && (
            <WorklistView
              bdId={bdId}
              bdName={activeBd?.name}
              config={config}
              dataVersion={dataVersion}
              onOpenLead={openLead}
              onChanged={refresh}
            />
          )}

          {safeView === 'leads' && (
            <LeadsView dataVersion={dataVersion} onOpenLead={openLead} onChanged={refresh} />
          )}

          {safeView === 'insights' && (
            <InsightsView dataVersion={dataVersion} />
          )}
        </main>
      </div>

      {detailLeadId && (
        <LeadDetailsModal
          lead={detail}
          loading={detailLoading}
          error={detailError}
          config={config}
          onAnalyzed={() => { openLead(detailLeadId); refresh() }}
          onClose={() => { setDetailLeadId(null); setDetail(null) }}
        />
      )}
    </div>
  )
}

/* Line icons rather than emoji: they inherit colour and size, and they do not
   render as someone else's picture on a different platform. */
const ico = {
  width: 16, height: 16, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor',
  strokeWidth: 1.8, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true,
}
const ListIcon = () => (
  <svg {...ico}><path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01" /></svg>
)
const TableIcon = () => (
  <svg {...ico}><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M3 10h18M9 10v10" /></svg>
)
const ChartIcon = () => (
  <svg {...ico}><path d="M3 3v18h18" /><path d="M7 15l4-5 3 3 5-7" /></svg>
)
