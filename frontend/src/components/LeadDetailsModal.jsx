import { badgeFor, formatDateTime, formatDuration, outcomeBadge } from './format.js'

export default function LeadDetailsModal({ lead, loading, error, onClose }) {
  const badge = lead ? badgeFor(lead) : null
  const outcome = lead ? outcomeBadge(lead.latest_outcome, lead.lead_status) : null
  const followUp = lead?.follow_up || {}

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <div>
            <h2>{lead?.name || 'Lead Details'}</h2>
            {lead && <div className="sub">{lead.lead_id} · {lead.course}</div>}
          </div>
          <button className="close-x" onClick={onClose} aria-label="Close">×</button>
        </div>

        <div className="modal-body">
          {loading && <p>Loading lead…</p>}
          {error && <div className="banner error">{error}</div>}

          {lead && !loading && (
            <>
              <section className="section">
                <h3>Lead Information</h3>
                <dl className="kv">
                  <dt>Name</dt><dd>{lead.name}</dd>
                  <dt>Phone</dt><dd>{lead.phone}</dd>
                  <dt>Email</dt><dd>{lead.email}</dd>
                  <dt>Course</dt><dd>{lead.course}</dd>
                  <dt>Assigned BD</dt><dd>{lead.assigned_bd?.name} ({lead.assigned_bd?.id})</dd>
                  <dt>Lead Status</dt><dd><span className={`badge ${badge.cls}`}>{lead.lead_status}</span></dd>
                </dl>
              </section>

              <section className="section">
                <h3>Latest Call</h3>
                {lead.latest_call ? (
                  <dl className="kv">
                    <dt>Call Date</dt><dd>{formatDateTime(lead.latest_call.ended_at)}</dd>
                    <dt>Duration</dt><dd>{formatDuration(lead.latest_call.duration_seconds)}</dd>
                    <dt>Outcome</dt>
                    <dd>
                      <span className={`badge ${outcome.cls}`}>{outcome.label}</span>
                      {lead.latest_call_outcome?.reason && (
                        <div className="sub" style={{ marginTop: 4 }}>{lead.latest_call_outcome.reason}</div>
                      )}
                    </dd>
                  </dl>
                ) : <p className="sub">No calls recorded yet.</p>}
              </section>

              <section className="section">
                <h3>Follow-up</h3>
                {followUp.required ? (
                  <dl className="kv">
                    <dt>Required</dt><dd>Yes</dd>
                    <dt>Date</dt><dd>{followUp.date || '—'}</dd>
                    <dt>Time</dt><dd>{followUp.time || '—'}</dd>
                    <dt>Status</dt>
                    <dd>
                      {followUp.status} <span className={`badge ${badge.cls}`}>{badge.label}</span>
                    </dd>
                  </dl>
                ) : (
                  <p className="sub">
                    No follow-up required{lead.latest_outcome ? ` (${lead.latest_outcome.replaceAll('_', ' ').toLowerCase()})` : ''}.
                  </p>
                )}
              </section>

              <section className="section">
                <h3>Latest Conversation</h3>
                {lead.latest_transcript ? (
                  <div className="transcript">“{lead.latest_transcript.transcript}”</div>
                ) : (
                  <p className="sub">No transcript available for this lead.</p>
                )}
              </section>

              {lead.follow_up_history?.length > 0 && (
                <section className="section">
                  <h3>Follow-up History</h3>
                  {lead.follow_up_history.map((entry, index) => (
                    <div className="history-item" key={index}>
                      <strong>{entry.action}</strong>
                      <span className="sub"> · {formatDateTime(entry.at)}</span>
                      <div>{entry.reason}</div>
                      {entry.new_datetime && (
                        <div className="sub">
                          {formatDateTime(entry.previous_datetime)} → {formatDateTime(entry.new_datetime)}
                        </div>
                      )}
                    </div>
                  ))}
                </section>
              )}
            </>
          )}
        </div>

        <div className="modal-foot">
          <button className="btn" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  )
}
