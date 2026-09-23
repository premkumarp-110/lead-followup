import { useCallback, useEffect, useRef, useState } from 'react'
import api from '../services/api.js'
import { formatDateTime, relativeToNow } from './format.js'

/**
 * Reminder bell + popover.
 *
 * The count is overdue + due-today for the selected BD, matching exactly what
 * the email digest would contain -- the panel and the inbox must never
 * disagree about how much is outstanding.
 *
 * Pending data is a read, so it loads even when FOLLOWUP_ALERTS_ENABLED is
 * off; only the send button depends on the flag.
 */
export default function ReminderBell({ bdId, config, dataVersion = 0, onOpenLead, onSent }) {
  const [open, setOpen] = useState(false)
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [sending, setSending] = useState(false)
  const [sendResult, setSendResult] = useState('')
  const [history, setHistory] = useState([])
  const wrapRef = useRef(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setData(await api.getPendingAlerts(bdId))
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [bdId])

  // dataVersion changes whenever a view actions a lead, so the count cannot
  // go stale while someone works through their queue.
  useEffect(() => { load() }, [load, dataVersion])

  // History only matters while the panel is open.
  useEffect(() => {
    if (!open) return
    api.getAlertHistory(bdId, 5).then(setHistory).catch(() => setHistory([]))
  }, [open, bdId])

  // Esc closes, and so does a click anywhere outside.
  useEffect(() => {
    if (!open) return
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false) }
    const onClick = (e) => { if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false) }
    document.addEventListener('keydown', onKey)
    document.addEventListener('mousedown', onClick)
    return () => {
      document.removeEventListener('keydown', onKey)
      document.removeEventListener('mousedown', onClick)
    }
  }, [open])

  const overdue = data?.total_overdue ?? 0
  const dueToday = data?.total_due_today ?? 0
  const count = overdue + dueToday

  const alertsOn = Boolean(config?.followup_alerts_enabled)
  const emailOk = Boolean(config?.followup_alerts_email_configured)
  const sendDisabled = !alertsOn || !emailOk || sending || count === 0
  const sendReason = !alertsOn
    ? 'Reminder emails are disabled (FOLLOWUP_ALERTS_ENABLED is not true in backend/.env).'
    : !emailOk
      ? 'SMTP is not configured on the backend, so nothing can be sent yet.'
      : count === 0
        ? 'Nothing is overdue or due today.'
        : bdId
          ? 'Send this BD their digest now.'
          : 'Send every BD with something pending their digest now. This makes one email per BD and can take a while.'

  async function send() {
    setSending(true)
    setSendResult('')
    try {
      const res = await api.sendDigest(bdId)
      const parts = []
      if (res.sent) parts.push(`${res.sent} sent`)
      if (res.failed) parts.push(`${res.failed} failed`)
      if (res.skipped) parts.push(`${res.skipped} skipped`)
      setSendResult(parts.length ? parts.join(' · ') : res.message || 'Nothing to send.')
      await load()
      api.getAlertHistory(bdId, 5).then(setHistory).catch(() => {})
      onSent?.(res)
    } catch (err) {
      setSendResult(err.message)
    } finally {
      setSending(false)
    }
  }

  const lastRun = history[0]

  return (
    <div className="bell-wrap" ref={wrapRef}>
      <button
        type="button"
        className={`icon-btn ${open ? 'active' : ''}`}
        onClick={() => setOpen((v) => !v)}
        aria-label={count ? `Reminders: ${count} need attention` : 'Reminders'}
        aria-expanded={open}
        title="Reminders"
      >
        <BellIcon />
        {/* A zero is not news -- no badge unless there is something to act on. */}
        {count > 0 && <span className="bell-badge">{count > 99 ? '99+' : count}</span>}
      </button>

      {open && (
        <div className="popover" role="dialog" aria-label="Pending follow-ups">
          <div className="popover-head">
            <h3>Reminders</h3>
            <span className="sub">{bdId ? 'Selected BD' : 'All BDs'}</span>
          </div>

          <div className="popover-body">
            {loading && !data && (
              <div style={{ padding: 16 }}>
                {[0, 1, 2].map((i) => <div key={i} className="skeleton skeleton-line" style={{ width: `${80 - i * 15}%` }} />)}
              </div>
            )}

            {error && <div className="empty"><div className="empty-title">Could not load reminders</div><div className="empty-hint">{error}</div></div>}

            {!loading && !error && count === 0 && (
              <div className="empty">
                <div className="empty-title">Nothing needs chasing</div>
                <div className="empty-hint">
                  No overdue follow-ups and nothing due today
                  {data?.total_unscheduled ? `. ${data.total_unscheduled} still need a date.` : '.'}
                </div>
              </div>
            )}

            {/* A group whose only pending work is unscheduled contributes
                nothing to the count, so its header would sit above an empty
                list. Those leads are surfaced in the totals instead. */}
            {!error && count > 0 && data.groups
              .filter((g) => g.overdue.length || g.due_today.length)
              .map((group) => (
              <div key={group.bd_id}>
                <div className="popover-group-label">
                  {group.bd_name} · {group.overdue.length} overdue · {group.due_today.length} due today
                </div>
                {[...group.overdue.map((l) => ['overdue', l]), ...group.due_today.map((l) => ['due', l])].map(
                  ([kind, lead]) => (
                    <button
                      key={`${kind}-${lead.lead_id}`}
                      type="button"
                      className="popover-item"
                      onClick={() => { setOpen(false); onOpenLead?.(lead.lead_id) }}
                    >
                      <span className={`badge ${kind}`}>{kind === 'overdue' ? 'Overdue' : 'Due'}</span>
                      <span className="grow">
                        <span className="lead-name mono truncate">{lead.lead_id}</span>
                        <span className="sub">{lead.product || lead.stage || '—'}</span>
                      </span>
                      <span className="sub">{relativeToNow(lead.follow_up?.datetime)}</span>
                    </button>
                  ),
                )}
              </div>
            ))}

            {!error && data?.unassigned_count > 0 && (
              <div className="banner warn" style={{ margin: 12 }}>
                {data.unassigned_count} pending follow-up{data.unassigned_count === 1 ? '' : 's'} have no
                assigned BD, so nobody can be reminded about them.
              </div>
            )}
          </div>

          <div className="popover-foot">
            <span title={lastRun ? formatDateTime(lastRun.sent_at) : ''}>
              {sendResult
                ? sendResult
                : lastRun
                  ? `Last run ${relativeToNow(lastRun.sent_at)} · ${lastRun.status.toLowerCase().replaceAll('_', ' ')}`
                  : 'No reminder sent yet'}
            </span>
            <button
              type="button"
              className="btn small primary"
              onClick={send}
              disabled={sendDisabled}
              title={sendReason}
            >
              {sending ? 'Sending…' : 'Send digest'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

function BellIcon() {
  return (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
      <path d="M13.73 21a2 2 0 0 1-3.46 0" />
    </svg>
  )
}
