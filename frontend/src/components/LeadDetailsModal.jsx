import { useState } from 'react'
import api from '../services/api.js'
import AudioPlayer from './AudioPlayer.jsx'
import {
  badgeFor, formatDateTime, formatDuration, formatFollowUp, formatPercent, formatProduct,
  formatScore, formatSentimentScore, intentBadge, orDash, outcomeBadge, sentimentBadge,
  stageStep, trajectoryMark,
} from './format.js'

/**
 * Everything known about one lead.
 *
 * There is no "Analyze Call" screen any more: the recording and often the
 * transcript already exist on the call, so analysis is an action on a call
 * here. A call only offers it when there is something to analyse -- a
 * not-connected, zero-duration call is a finished state, not a backlog item.
 */
export default function LeadDetailsModal({ lead, loading, error, config, onAnalyzed, onClose }) {
  const [busyCall, setBusyCall] = useState(null)
  const [callError, setCallError] = useState('')
  const [notice, setNotice] = useState('')
  const [reminding, setReminding] = useState(false)

  // Reminders need the feature on AND a working mailer.
  const canRemind = Boolean(
    config?.followup_alerts_enabled && config?.followup_alerts_email_configured,
  )

  const badge = lead ? badgeFor(lead) : null
  const outcome = lead ? outcomeBadge(lead.latest_outcome, lead.lead_status) : null
  const followUp = lead?.follow_up || {}
  const analysis = lead?.latest_analysis
  const intent = analysis ? intentBadge(analysis.customer_intent) : null
  const tone = analysis?.sentiment ? sentimentBadge(analysis.sentiment) : null
  const traj = trajectoryMark(analysis?.sentiment)
  const toneScore = formatSentimentScore(analysis?.sentiment)
  const calls = lead?.calls || []

  async function analyse(call) {
    setBusyCall(call.call_id)
    setCallError('')
    setNotice('')
    try {
      const result = await api.analyzeCall(call.call_id)
      setNotice(result.message)
      onAnalyzed?.()
    } catch (err) {
      setCallError(err.message)
    } finally {
      setBusyCall(null)
    }
  }

  async function remind() {
    setReminding(true)
    setCallError('')
    setNotice('')
    try {
      const result = await api.sendLeadReminder(lead.lead_id)
      setNotice(result.message || 'Reminder sent.')
    } catch (err) {
      setCallError(err.message)
    } finally {
      setReminding(false)
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal wide" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <div>
            {/* The CRM exposes no name -- the id is the identity. */}
            <h2 className="mono">{lead?.lead_id || 'Lead Details'}</h2>
            {lead && (
              <div className="sub">
                {formatProduct(lead.product)}
                {lead.stage ? ` · ${lead.stage}` : ''}
                {lead.owner_name ? ` · ${lead.owner_name}` : ''}
              </div>
            )}
          </div>
          <button className="close-x" onClick={onClose} aria-label="Close">×</button>
        </div>

        <div className="modal-body">
          {loading && <p>Loading lead…</p>}
          {error && <div className="banner error">{error}</div>}
          {notice && <div className="banner info">{notice}</div>}
          {callError && <div className="banner error">{callError}</div>}

          {lead && !loading && (
            <>
              <section className="section">
                <h3>Lead</h3>
                <dl className="kv">
                  <dt>Lead ID</dt><dd className="mono">{lead.lead_id}</dd>
                  <dt>External ID</dt>
                  <dd className="mono">
                    {lead.external_id || <span className="sub">none</span>}
                    <div className="sub">The LeadSquared GUID — the cross-system join key.</div>
                  </dd>
                  <dt>Stage</dt>
                  <dd>
                    {lead.stage ? (
                      <span className={`chip ${stageStep(lead.stage) >= 3 ? 'chip-warn' : ''}`}>
                        {lead.stage}
                      </span>
                    ) : '—'}
                    {lead.previous_stage && <span className="sub"> · was {lead.previous_stage}</span>}
                  </dd>
                  <dt>Product</dt><dd>{formatProduct(lead.product)}</dd>
                  <dt>Language</dt><dd>{orDash(lead.language)}</dd>
                  <dt>Location</dt>
                  <dd>{[lead.city, lead.state, lead.country].filter(Boolean).join(', ') || '—'}</dd>
                  <dt>Source</dt>
                  <dd>
                    {orDash(lead.lead_source)}
                    {lead.source_campaign && <span className="sub"> · {lead.source_campaign}</span>}
                  </dd>
                  <dt>Segmentation</dt><dd>{orDash(lead.segmentation)}</dd>
                  <dt>Win probability</dt><dd>{formatScore(lead.win_probability)}</dd>
                  <dt>Owner (BDA)</dt>
                  <dd>{orDash(lead.owner_name)}{lead.owner_email && <span className="sub"> · {lead.owner_email}</span>}</dd>
                  <dt>Lead Status</dt>
                  <dd><span className={`badge ${badge.cls}`}>{lead.lead_status.replaceAll('_', ' ')}</span></dd>
                </dl>
              </section>

              <section className="section">
                <h3>Call activity</h3>
                <dl className="kv">
                  <dt>Attempts</dt>
                  <dd>{lead.total_attempts} total · {lead.calls_connected} connected · {lead.calls_missed} missed</dd>
                  <dt>Talk time</dt><dd>{formatDuration(lead.total_talktime_sec)}</dd>
                  <dt>Last attempt</dt><dd>{lead.last_call_attempted_at ? formatDateTime(lead.last_call_attempted_at) : '—'}</dd>
                  <dt>First contact</dt><dd>{lead.first_contact_date ? formatDateTime(lead.first_contact_date) : '—'}</dd>
                  <dt>Disposition</dt>
                  <dd>
                    {orDash(lead.last_disposition_status)}
                    {lead.last_sub_disposition_status && <span className="sub"> · {lead.last_sub_disposition_status}</span>}
                  </dd>
                </dl>
              </section>

              <section className="section">
                <h3>Calls ({calls.length})</h3>
                {calls.length ? calls.map((call) => (
                  <CallCard
                    key={call.call_id}
                    call={call}
                    config={config}
                    busy={busyCall === call.call_id}
                    disabled={Boolean(busyCall)}
                    onAnalyse={() => analyse(call)}
                  />
                )) : <p className="sub">No calls recorded for this lead.</p>}
              </section>

              <section className="section">
                <h3>Analysis</h3>
                {analysis ? (
                  <>
                    {analysis.degraded && (
                      <div className="banner warn" style={{ marginBottom: 10 }}>
                        Produced by the deterministic fallback analyzer — the analysis model was
                        unavailable for this call. Treat the outcome as provisional.
                      </div>
                    )}
                    <dl className="kv">
                      <dt>Outcome</dt><dd><span className={`badge ${outcome.cls}`}>{outcome.label}</span></dd>
                      <dt>Customer Intent</dt><dd><span className={`badge ${intent.cls}`}>{intent.label}</span></dd>
                      <dt>Sentiment</dt>
                      <dd>
                        {tone ? (
                          <>
                            <span className={`badge ${tone.cls}`}>
                              {tone.label}
                              {traj && <span className={traj.cls} title={traj.title}>{traj.mark}</span>}
                            </span>
                            {toneScore && <span className="sub"> · {toneScore}</span>}
                            {traj && <span className="sub"> · {traj.title.toLowerCase()}</span>}
                            {analysis.sentiment.evidence && (
                              <div className="sentiment-evidence">“{analysis.sentiment.evidence}”</div>
                            )}
                            {analysis.sentiment.label === 'UNKNOWN' && (
                              <div className="sub">
                                {analysis.degraded
                                  ? 'The fallback analyzer does not judge tone.'
                                  : 'The call was too short or unclear to judge.'}
                              </div>
                            )}
                          </>
                        ) : (
                          <span className="sub">Not assessed</span>
                        )}
                      </dd>
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
                ) : (
                  <p className="sub">
                    Not analysed yet. Use Analyse on a call above that has a transcript or a recording.
                  </p>
                )}
              </section>

              <section className="section">
                <h3>Follow-up</h3>
                {followUp.required ? (
                  <dl className="kv">
                    <dt>Due</dt>
                    <dd>
                      {followUp.datetime ? formatFollowUp(followUp) : 'No date was stated on the call'}
                      {!followUp.datetime && (
                        <div className="sub">
                          The analyzer never invents a date. This lead still needs action — it just
                          cannot be placed on the timeline yet.
                        </div>
                      )}
                    </dd>
                    <dt>Status</dt>
                    <dd>{followUp.status} <span className={`badge ${badge.cls}`}>{badge.label}</span></dd>
                    <dt>Reason</dt><dd>{followUp.reason || '—'}</dd>
                    <dt>Remind</dt>
                    <dd>
                      {/* One lead, to its owner, now. Unlike the daily digest
                          this works for an upcoming or undated follow-up. */}
                      <button
                        type="button"
                        className="btn small"
                        onClick={remind}
                        disabled={!canRemind || reminding}
                        title={canRemind
                          ? `Email a reminder about this lead to ${lead.owner_name || 'its owner'}`
                          : 'Reminders are off, or SMTP is not configured on the server'}
                      >
                        {reminding ? 'Sending…' : 'Email a reminder'}
                      </button>
                      {!canRemind && (
                        <div className="sub">
                          Reminders are disabled or SMTP is not configured on the server.
                        </div>
                      )}
                      {canRemind && (
                        <div className="sub">
                          Goes to {lead.owner_name || 'the lead owner'}. See{' '}
                          <a href={api.leadReminderPreviewUrl(lead.lead_id)} target="_blank" rel="noreferrer">
                            what it looks like
                          </a>{' '}
                          without sending.
                        </div>
                      )}
                    </dd>
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
                      Source: {lead.latest_transcript.source || lead.latest_transcript.provider || 'unknown'}
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

/** One call, with whatever the CRM already worked out about it. */
function CallCard({ call, config, busy, disabled, onAnalyse }) {
  const [showTips, setShowTips] = useState(false)
  const crm = call.analysis_summary_parsed
  const tips = crm?.findings?.improvement_tips || []
  const violations = crm?.findings?.violations || []
  const connected = call.telephony_status === 'connected'
  // Analysis needs source material. Without it there is nothing to spend a
  // model call on, so the button is not offered at all.
  const analysable = Boolean(call.has_transcript || call.has_recording)

  const statusCls = call.status === 'COMPLETED' ? 'converted'
    : call.status === 'FAILED' ? 'overdue'
    : call.status === 'NOT_ANALYZABLE' ? 'neutral' : 'upcoming'

  return (
    <div className="call-card">
      <div className="call-card-head">
        <div>
          <span className="mono">{call.call_id}</span>
          <span className="sub"> · {formatDateTime(call.call_time || call.created_at)}</span>
        </div>
        <span className={`badge ${statusCls}`}>{call.status.replaceAll('_', ' ')}</span>
      </div>

      <div className="call-meta">
        <span>{call.direction === 'inbound' ? '↙ Inbound' : '↗ Outbound'}</span>
        <span>{formatDuration(call.duration_sec)}</span>
        <span className={connected ? '' : 'sub'}>{orDash(call.telephony_status).replaceAll('_', ' ')}</span>
        <span className="sub">{call.owner_name || '—'}</span>
        {call.pitch_score != null && <span title="CRM pitch score">Pitch {formatScore(call.pitch_score)}</span>}
        {call.violations > 0 && (
          <span className="call-violation">{call.violations} violation{call.violations === 1 ? '' : 's'}</span>
        )}
      </div>

      <div className="call-flags">
        <span className={call.has_transcript ? 'flag on' : 'flag'}>
          {call.has_transcript ? '✓' : '×'} transcript
        </span>
        <span className={call.has_recording ? 'flag on' : 'flag'}>
          {call.has_recording ? '✓' : '×'} recording
        </span>
      </div>

      {call.error && <div className="sub" style={{ marginTop: 6 }}>{call.error}</div>}

      {call.has_recording && <AudioPlayer call={call} config={config} />}

      {violations.length > 0 && (
        <ul className="violations">
          {violations.map((v, i) => (
            <li key={i}>
              <span className={`sev sev-${(v.severity || '').toLowerCase()}`}>{v.severity}</span>
              <strong>{v.issue}</strong>
              <div className="sub">{v.detail}</div>
            </li>
          ))}
        </ul>
      )}

      {tips.length > 0 && (
        <div className="call-tips">
          <button type="button" className="btn small subtle" onClick={() => setShowTips((v) => !v)}>
            {showTips ? 'Hide' : 'Show'} {tips.length} coaching tip{tips.length === 1 ? '' : 's'}
          </button>
          {showTips && <ul className="points">{tips.map((t, i) => <li key={i}>{t}</li>)}</ul>}
        </div>
      )}

      <div className="call-actions">
        {analysable ? (
          <button type="button" className="btn small primary" disabled={disabled} onClick={onAnalyse}>
            {busy ? 'Analysing…' : call.status === 'COMPLETED' ? 'Re-analyse' : 'Analyse'}
          </button>
        ) : (
          <span className="sub">Nothing to analyse — no transcript and no recording.</span>
        )}
      </div>
    </div>
  )
}
