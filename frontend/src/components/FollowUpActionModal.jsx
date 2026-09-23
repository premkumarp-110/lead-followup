import { useState } from 'react'

// Rescheduling is the only follow-up action left. Marking done and cancelling
// are both retired: a BD closing a follow-up by hand clears the queue without
// anything having happened, so closure belongs to the call analysis. The
// backend refuses both -- their enum values survive only so leads acted on
// before the change still deserialise.
const TITLES = {
  RESCHEDULE: 'Reschedule Follow-up',
}

/** Converts an ISO instant into the value format <input type="datetime-local"> expects. */
function toLocalInput(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

export default function FollowUpActionModal({ lead, action, onClose, onSubmit }) {
  const [reason, setReason] = useState('')
  const [newDateTime, setNewDateTime] = useState(toLocalInput(lead.follow_up?.datetime))
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  const isReschedule = action === 'RESCHEDULE'

  async function handleSubmit(event) {
    event.preventDefault()
    if (!reason.trim()) {
      setError('A reason is required so the team knows why this action was taken.')
      return
    }
    if (isReschedule && !newDateTime) {
      setError('Pick the new follow-up date and time.')
      return
    }

    setSaving(true)
    setError('')
    try {
      await onSubmit({
        action,
        reason: reason.trim(),
        ...(isReschedule ? { new_datetime: new Date(newDateTime).toISOString() } : {}),
      })
    } catch (err) {
      setError(err.message)
      setSaving(false)
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal small" onClick={(e) => e.stopPropagation()}>
        <form onSubmit={handleSubmit}>
          <div className="modal-head">
            <div>
              <h2>{TITLES[action]}</h2>
              <div className="sub"><span className="mono">{lead.lead_id}</span>{lead.product ? ` · ${lead.product}` : ''}</div>
            </div>
            <button type="button" className="close-x" onClick={onClose} aria-label="Close">×</button>
          </div>

          <div className="modal-body">
            {error && <div className="form-error">{error}</div>}

            {isReschedule && (
              <div className="form-field">
                <label htmlFor="new-dt">New follow-up date &amp; time</label>
                <input
                  id="new-dt"
                  type="datetime-local"
                  value={newDateTime}
                  onChange={(e) => setNewDateTime(e.target.value)}
                />
              </div>
            )}

            <div className="form-field">
              <label htmlFor="reason">Reason (required)</label>
              <textarea
                id="reason"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder={
                  'e.g. Lead was travelling, agreed a new slot.'
                }
              />
            </div>
          </div>

          <div className="modal-foot">
            <button type="button" className="btn" onClick={onClose} disabled={saving}>Close</button>
            <button type="submit" className="btn primary" disabled={saving}>
              {saving ? 'Saving…' : 'Confirm'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
