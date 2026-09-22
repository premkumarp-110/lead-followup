import AudioPlayer from './AudioPlayer.jsx'
import {
  badgeFor, formatDateTime, formatDuration, formatFollowUp, formatPercent, intentBadge, outcomeBadge,
} from './format.js'

export default function LeadDetailsModal({ lead, loading, error, config, onClose }) {
  const badge = lead ? badgeFor(lead) : null
  const outcome = lead ? outcomeBadge(lead.latest_outcome, lead.lead_status) : null
  const followUp = lead?.follow_up || {}
  const analysis = lead?.latest_analysis
  const call = lead?.latest_call
  const intent = analysis ? intentBadge(analysis.customer_intent) : null

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal wide" onClick={(e) => e.stopPropagation()}>
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
                <h3>Lead Details</h3>
                <dl className="kv">
                  <dt>Name</dt><dd>{lead.name}</dd>
                  <dt>Phone</dt><dd>{lead.phone}</dd>
                  <dt>Email</dt><dd>{lead.email}</dd>
                  <dt>Course</dt><dd>{lead.course}</dd>
                  <dt>Assigned BD</dt><dd>{lead.assigned_bd?.name} ({lead.assigned_bd?.id})</dd>
                  <dt>Lead Status</dt><dd><span className={`badge ${badge.cls}`}>{lead.lead_status.replaceAll('_', ' ')}</span></dd>
                </dl>
              </section>

              <section className="section">
                <h3>Latest Call</h3>
                {call ? (
                  <>
                    <dl className="kv">
                      <dt>Call Date</dt><dd>{formatDateTime(call.ended_at || call.created_at)}</dd>
                      <dt>Caller</dt>
                      <dd>{lead.latest_caller ? `${lead.latest_caller.name} (${lead.latest_caller.caller_id})` : call.caller_id || '—'}</dd>
                      <dt>Duration</dt><dd>{formatDuration(call.duration_seconds)}</dd>
                      <dt>Audio Source</dt>
                      <dd>
                        {call.source_type === 'URL' ? 'Audio URL' : 'Uploaded file'}
                        {call.audio_filename && <span className="sub"> · {call.audio_filename}</span>}
                      </dd>
                      <dt>Processing</dt>
                      <dd>
                        <span className={`badge ${call.status === 'COMPLETED' ? 'converted' : call.status === 'FAILED' ? 'overdue' : 'neutral'}`}>
                          {call.status}
                        </span>
                        {call.error && <div className="sub" style={{ marginTop: 4 }}>{call.error}</div>}
                      </dd>
                    </dl>
                    <div style={{ marginTop: 10 }}>
                      <AudioPlayer call={call} config={config} />
                    </div>
                  </>
                ) : <p className="sub">No calls recorded yet.</p>}
              </section>

              <section className="section">
                <h3>AI Analysis</h3>
                {analysis ? (
                  <>
                    {analysis.degraded && (
                      <div className="banner warn" style={{ marginBottom: 10 }}>
                        Produced by the deterministic fallback analyzer — Gemini was unavailable for this call.
                      </div>
                    )}
                    <dl className="kv">
                      <dt>Outcome</dt><dd><span className={`badge ${outcome.cls}`}>{outcome.label}</span></dd>
                      <dt>Customer Intent</dt><dd><span className={`badge ${intent.cls}`}>{intent.label}</span></dd>
                      <dt>Confidence</dt>
                      <dd>{formatPercent(analysis.confidence)} <span className="sub">· {analysis.model}</span></dd>
                      <dt>Summary</dt><dd>{analysis.summary || '—'}</dd>
                      <dt>Key Points</dt>
                      <dd>
                        {analysis.key_points?.length
                          ? <ul className="points">{analysis.key_points.map((p, i) => <li key={i}>{p}</li>)}</ul>
                          : '—'}
                      </dd>
                    </dl>
                  </>
                ) : <p className="sub">No analysis yet — process a call for this lead.</p>}
              </section>

              <section className="section">
                <h3>Follow-up</h3>
                {followUp.required ? (
                  <dl className="kv">
                    <dt>Required</dt><dd>Yes</dd>
                    <dt>Date</dt><dd>{followUp.datetime ? formatFollowUp(followUp).split(',')[0] : 'Not specified'}</dd>
                    <dt>Time</dt><dd>{followUp.datetime ? formatFollowUp(followUp).split(', ').slice(1).join(', ') : 'Not specified'}</dd>
                    <dt>Status</dt>
                    <dd>{followUp.status} <span className={`badge ${badge.cls}`}>{badge.label}</span></dd>
                    <dt>Reason</dt><dd>{followUp.reason || '—'}</dd>
                  </dl>
                ) : (
                  <p className="sub">
                    No follow-up required{lead.latest_outcome ? ` (${lead.latest_outcome.replaceAll('_', ' ').toLowerCase()})` : ''}.
                  </p>
                )}
              </section>

              <section className="section">
                <h3>Latest Transcript</h3>
                {lead.latest_transcript ? (
                  <>
                    <div className="transcript">{lead.latest_transcript.transcript}</div>
                    <div className="sub" style={{ marginTop: 6 }}>
                      {lead.latest_transcript.language && <>Language: {lead.latest_transcript.language} · </>}
                      Transcribed by {lead.latest_transcript.provider || 'unknown'}
                    </div>
                  </>
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
