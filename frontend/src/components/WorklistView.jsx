import { useCallback, useEffect, useMemo, useState } from 'react'
import api from '../services/api.js'
import FollowUpActionModal from './FollowUpActionModal.jsx'
import WorklistRow from './WorklistRow.jsx'
import { isIstToday } from './format.js'

const PAGE = 25

/**
 * The BD's queue: who to call next, in order.
 *
 * Sections mirror the reminder digest exactly -- Overdue, Due today, then
 * Unscheduled -- so the screen and the email never tell different stories.
 * "Due today" includes anything inside the backend's 2-hour DUE window even
 * when it falls just past IST midnight, which is the same rule
 * followup_alert_service applies.
 *
 * Ordering comes from GET /api/leads/follow-ups, which the backend already
 * returns worklist-sorted via sort_worklist(); this does not re-sort.
 */
export default function WorklistView({ bdId, bdName, config, dataVersion, onOpenLead, onChanged }) {
  const [leads, setLeads] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [pendingAction, setPendingAction] = useState(null)
  const [savingId, setSavingId] = useState(null)
  const [rowErrors, setRowErrors] = useState({})
  const [shown, setShown] = useState(PAGE)
  // Per-row reminder state: 'sending' | 'sent'. Keyed by lead_id so one row's
  // send never blocks or relabels another.
  const [remindState, setRemindState] = useState({})

  // Reminders need the feature on AND a working mailer. Without both, the
  // button explains itself rather than failing on click.
  const canRemind = Boolean(
    config?.followup_alerts_enabled && config?.followup_alerts_email_configured,
  )

  const remindLead = useCallback(async (lead) => {
    setRemindState((s) => ({ ...s, [lead.lead_id]: 'sending' }))
    setRowErrors((e) => ({ ...e, [lead.lead_id]: undefined }))
    try {
      const result = await api.sendLeadReminder(lead.lead_id)
      if (result.sent) {
        setRemindState((s) => ({ ...s, [lead.lead_id]: 'sent' }))
      } else {
        // Skipped rather than sent -- e.g. the owner is inactive. Say which.
        setRemindState((s) => ({ ...s, [lead.lead_id]: undefined }))
        setRowErrors((e) => ({ ...e, [lead.lead_id]: result.message || 'Reminder was not sent.' }))
      }
    } catch (err) {
      setRemindState((s) => ({ ...s, [lead.lead_id]: undefined }))
      setRowErrors((e) => ({ ...e, [lead.lead_id]: err.message }))
    }
  }, [])

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      // The BD filter accepts a caller id; the shell stores exactly that.
      setLeads(await api.getFollowUps(bdId ? { bd: bdId } : {}))
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [bdId])

  useEffect(() => { load() }, [load, dataVersion])
  useEffect(() => { setShown(PAGE) }, [bdId])

  const groups = useMemo(() => {
    const out = { overdue: [], due: [], unscheduled: [] }
    for (const lead of leads) {
      const bucket = lead.follow_up?.bucket
      if (bucket === 'OVERDUE') out.overdue.push(lead)
      else if (bucket === 'DUE') out.due.push(lead)
      else if (bucket === 'UPCOMING' && isIstToday(lead.follow_up?.datetime)) out.due.push(lead)
      else if (bucket === 'UNSCHEDULED') out.unscheduled.push(lead)
    }
    return out
  }, [leads])

  const laterCount = leads.length - groups.overdue.length - groups.due.length - groups.unscheduled.length
  const actionable = groups.overdue.length + groups.due.length

  async function submitAction(payload) {
    const leadId = pendingAction.lead.lead_id
    setSavingId(leadId)
    setRowErrors((e) => ({ ...e, [leadId]: undefined }))
    try {
      await api.updateFollowUp(leadId, payload)
      setPendingAction(null)
      // No optimistic removal. That was right when Done existed -- the lead
      // really did leave the queue -- but reschedule is the only action now and
      // a rescheduled lead is still PENDING. Dropping it here would make the
      // row vanish and then reappear when load() resolves. Let the refetch
      // move it to its new position instead.
      onChanged?.()
      load()
    } catch (err) {
      // Keep the row and attach the error to it: losing the row on failure
      // would look like the action succeeded.
      setRowErrors((e) => ({ ...e, [leadId]: err.message }))
      throw err
    } finally {
      setSavingId(null)
    }
  }

  if (loading && !leads.length) return <WorklistSkeleton />

  if (error) {
    return (
      <div className="panel">
        <div className="empty">
          <div className="empty-title">Could not load the worklist</div>
          <div className="empty-hint">{error}</div>
          <div className="empty-actions"><button className="btn" onClick={load}>Try again</button></div>
        </div>
      </div>
    )
  }

  if (!leads.length) {
    return (
      <div className="panel">
        <div className="empty">
          <div className="empty-title">
            {bdName ? `${bdName} is all clear` : 'Nothing needs follow-up'}
          </div>
          <div className="empty-hint">
            {bdId
              ? 'No pending follow-ups are assigned to this BD. Switch to All BDs to see the whole team.'
              : 'No lead currently has a pending follow-up.'}
          </div>
        </div>
      </div>
    )
  }

  return (
    <>
      <div className="banner info with-action">
        <span>
          <strong>{actionable}</strong> to action now
          {groups.unscheduled.length > 0 && <> · {groups.unscheduled.length} needing a date</>}
          {laterCount > 0 && <> · {laterCount} scheduled later</>}
        </span>
        <button className="btn small subtle" onClick={load} disabled={loading}>
          {loading ? 'Refreshing…' : 'Refresh'}
        </button>
      </div>

      <Section
        kind="overdue"
        title="Overdue"
        hint="Hardest first, then oldest"
        leads={groups.overdue}
        {...{ shown, savingId, rowErrors, onOpenLead, setPendingAction, remindLead, remindState, canRemind }}
      />
      <Section
        kind="due"
        title="Due today"
        hint="Hardest first, then soonest"
        leads={groups.due}
        {...{ shown, savingId, rowErrors, onOpenLead, setPendingAction, remindLead, remindState, canRemind }}
      />
      <Section
        kind="unscheduled"
        title="Needs a date"
        hint="Follow-up required, but the lead never named a time"
        leads={groups.unscheduled}
        {...{ shown, savingId, rowErrors, onOpenLead, setPendingAction, remindLead, remindState, canRemind }}
      />

      {leads.length > shown && (
        <button className="btn block" onClick={() => setShown((n) => n + PAGE)}>
          Show more ({leads.length - shown} remaining)
        </button>
      )}

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

function Section({
  kind, title, hint, leads, shown, savingId, rowErrors, onOpenLead, setPendingAction,
  remindLead, remindState, canRemind,
}) {
  // An empty section hides rather than showing a zero -- the queue should
  // read as what is left, not as a scorecard of empty boxes.
  if (!leads.length) return null
  const visible = leads.slice(0, shown)

  return (
    <section className="worklist-section">
      <div className={`worklist-head ${kind}`}>
        <h2>{title}</h2>
        <span className="count">{leads.length}</span>
        <span className="hint">{hint}</span>
      </div>
      <div className="worklist">
        {visible.map((lead) => (
          <WorklistRow
            key={lead.lead_id}
            lead={lead}
            kind={kind}
            saving={savingId === lead.lead_id}
            error={rowErrors[lead.lead_id]}
            onOpen={onOpenLead}
            onAction={(l, action) => setPendingAction({ lead: l, action })}
            onRemind={remindLead}
            remindState={remindState[lead.lead_id]}
            canRemind={canRemind}
          />
        ))}
      </div>
    </section>
  )
}

function WorklistSkeleton() {
  return (
    <>
      <div className="skeleton" style={{ height: 38, marginBottom: 16 }} />
      {[0, 1].map((s) => (
        <section className="worklist-section" key={s}>
          <div className="skeleton skeleton-line" style={{ width: 140, marginBottom: 8 }} />
          <div className="worklist">
            {[0, 1, 2].map((i) => (
              <div className="skeleton-row" key={i}>
                <span className="skeleton" style={{ flex: 1.3 }} />
                <span className="skeleton" style={{ flex: 1 }} />
                <span className="skeleton" style={{ flex: 1.8 }} />
                <span className="skeleton" style={{ width: 180 }} />
              </div>
            ))}
          </div>
        </section>
      ))}
    </>
  )
}
