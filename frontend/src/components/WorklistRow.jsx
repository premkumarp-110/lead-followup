import {
  formatDateTime, formatProduct, orDash, overdueBy, relativeToNow,
  sentimentBadge, stageStep, trajectoryMark,
} from './format.js'

/**
 * One line of the BD's queue.
 *
 * Actions are View / Reschedule / Remind. There is no Done and no Cancel: a BD
 * must not close a follow-up by asserting it is handled, because that empties
 * the queue without anything having happened. Closing is driven by analysing a
 * newer call for the lead, whose outcome decides. Reschedule only moves the
 * time; the backend refuses the other two.
 *
 * Every row answers three questions without a click: which lead, when it was
 * due, and WHY -- the reason the analysis extracted from the last call. A
 * worklist that only shows an id and a date makes the BD open each lead just
 * to remember what it was about.
 *
 * There is no name and no phone: the CRM exposes neither, so identity is the
 * lead id with product, stage and owner as context. The stage matters more
 * than it looks -- "DNP 5" is a very different lead from "DNP 1", and that
 * ladder is the CRM's own escalation signal.
 */
export default function WorklistRow({
  lead, kind, saving, error, onOpen, onAction, onRemind, remindState, canRemind,
}) {
  const when = lead.follow_up?.datetime
  const late = kind === 'overdue'
  const reason = lead.follow_up?.reason
  const step = stageStep(lead.stage)
  // Shown because it reorders this list: without it, a 3h-overdue row sitting
  // above a 2d-overdue one looks like a bug rather than a decision.
  const sentiment = lead.latest_sentiment
  const tone = sentiment ? sentimentBadge(sentiment) : null
  const traj = trajectoryMark(sentiment)

  return (
    <div className={`wl-row ${late ? 'urgent' : ''} ${saving ? 'saving' : ''}`}>
      <div style={{ minWidth: 0 }}>
        <div className="lead-name mono truncate" title={lead.lead_id}>
          {lead.lead_id}
        </div>
        <div className="wl-id">
          {formatProduct(lead.product)}
          {lead.owner_name ? ` · ${lead.owner_name}` : ''}
        </div>
        {lead.stage && (
          <div className="wl-stage">
            <span className={`chip ${step && step >= 3 ? 'chip-warn' : ''}`}>{lead.stage}</span>
            {lead.total_attempts > 0 && (
              <span className="wl-attempts" title="Total call attempts / connected">
                {lead.calls_connected}/{lead.total_attempts} connected
              </span>
            )}
            {tone && (
              <span className={`badge ${tone.cls}`} title="How the lead sounded on the last call">
                {tone.label}
                {traj && <span className={traj.cls} title={traj.title}>{traj.mark}</span>}
              </span>
            )}
          </div>
        )}
      </div>

      <div className={`wl-when ${late ? 'late' : ''}`}>
        {when ? (
          <>
            {/* Relative for urgency, absolute underneath for trust. */}
            <span className="rel">{late ? overdueBy(when) : relativeToNow(when)}</span>
            <span className="abs">{formatDateTime(when)}</span>
          </>
        ) : (
          <>
            <span className="rel">No date</span>
            <span className="abs">Needs one setting</span>
          </>
        )}
        {lead.language && <span className="abs">Speaks {orDash(lead.language)}</span>}
      </div>

      <div className="wl-why" title={reason || ''}>
        {reason || <span className="none">No reason recorded on the last analysis</span>}
      </div>

      <div className="wl-actions">
        <button type="button" className="btn small" onClick={() => onOpen(lead.lead_id)}>View</button>
        <button type="button" className="btn small primary" onClick={() => onAction(lead, 'RESCHEDULE')}>Reschedule</button>
        {/* Emails this one lead to its owner, now -- separate from the daily
            digest, and available even for a follow-up with no date. */}
        <button
          type="button"
          className="btn small subtle"
          onClick={() => onRemind(lead)}
          disabled={!canRemind || remindState === 'sending'}
          title={canRemind
            ? 'Email a reminder about this lead to its owner'
            : 'Reminders are off, or SMTP is not configured on the server'}
        >
          {remindState === 'sending' ? 'Sending…' : remindState === 'sent' ? 'Sent ✓' : 'Remind'}
        </button>
      </div>

      {error && <div className="wl-error">{error}</div>}
    </div>
  )
}
