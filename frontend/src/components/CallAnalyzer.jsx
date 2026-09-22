import { useEffect, useMemo, useRef, useState } from 'react'
import api from '../services/api.js'
import ProcessingSteps from './ProcessingSteps.jsx'
import {
  formatBytes, formatDuration, formatFollowUp, formatPercent, intentBadge, outcomeLabel,
} from './format.js'

const POLL_MS = 1000

/**
 * "Analyze New Call": audio in (upload or URL) -> lead + caller -> process.
 *
 * Owns only the analyzer's own state. When a call finishes, `onCompleted` tells
 * the dashboard to refetch so the new outcome shows up in the tables.
 */
export default function CallAnalyzer({ config, onCompleted, onOpenLead }) {
  const [mode, setMode] = useState('upload')

  // Upload mode
  const [file, setFile] = useState(null)
  const [fileDuration, setFileDuration] = useState(null)
  const [fileError, setFileError] = useState('')
  const fileInput = useRef(null)

  // URL mode
  const [audioUrl, setAudioUrl] = useState('')
  const [urlMeta, setUrlMeta] = useState(null)
  const [urlChecking, setUrlChecking] = useState(false)
  const [urlError, setUrlError] = useState('')

  // Association
  const [leads, setLeads] = useState([])
  const [callers, setCallers] = useState([])
  const [leadId, setLeadId] = useState('')
  const [callerId, setCallerId] = useState('')

  // Processing
  const [stage, setStage] = useState('idle')
  const [failedStage, setFailedStage] = useState(null)
  const [uploadPct, setUploadPct] = useState(0)
  const [error, setError] = useState('')
  const [result, setResult] = useState(null)
  const [showTranscript, setShowTranscript] = useState(false)
  const pollRef = useRef(null)

  useEffect(() => {
    api.getLeads().then((all) => setLeads([...all].sort((a, b) => a.name.localeCompare(b.name)))).catch(() => {})
    api.getCallers().then(setCallers).catch(() => {})
  }, [])

  useEffect(() => () => stopPolling(), [])

  const busy = !['idle', 'COMPLETED', 'FAILED'].includes(stage)
  const lead = useMemo(() => leads.find((l) => l.lead_id === leadId), [leads, leadId])
  const caller = useMemo(() => callers.find((c) => c.caller_id === callerId), [callers, callerId])
  const allowed = config?.allowed_audio_types || ['mp3', 'wav', 'm4a', 'ogg', 'webm']
  const maxMb = config?.max_audio_mb || 20
  const uploadDisabled = config?.audio_storage_mode === 'url'

  useEffect(() => { if (uploadDisabled) setMode('url') }, [uploadDisabled])

  // ---- file handling -------------------------------------------------------

  function pickFile(event) {
    const chosen = event.target.files?.[0]
    resetOutcome()
    setFile(null); setFileDuration(null); setFileError('')
    if (!chosen) return

    const ext = chosen.name.split('.').pop().toLowerCase()
    if (!allowed.includes(ext)) {
      setFileError(`Unsupported file type ".${ext}". Allowed: ${allowed.map((a) => '.' + a).join(', ')}.`)
      event.target.value = ''
      return
    }
    if (chosen.size > maxMb * 1024 * 1024) {
      setFileError(`File is ${formatBytes(chosen.size)}; the maximum is ${maxMb} MB.`)
      event.target.value = ''
      return
    }
    if (chosen.size === 0) {
      setFileError('The selected file is empty.')
      event.target.value = ''
      return
    }
    setFile(chosen)

    // Duration is read in the browser when the format is decodable; otherwise
    // the backend fills it in with ffprobe after upload.
    const probe = document.createElement('audio')
    const objectUrl = URL.createObjectURL(chosen)
    probe.preload = 'metadata'
    probe.onloadedmetadata = () => {
      if (Number.isFinite(probe.duration)) setFileDuration(probe.duration)
      URL.revokeObjectURL(objectUrl)
    }
    probe.onerror = () => URL.revokeObjectURL(objectUrl)
    probe.src = objectUrl
  }

  function clearFile() {
    setFile(null); setFileDuration(null); setFileError('')
    if (fileInput.current) fileInput.current.value = ''
    resetOutcome()
  }

  // ---- URL handling --------------------------------------------------------

  async function validateUrl() {
    setUrlError(''); setUrlMeta(null); resetOutcome()
    if (!audioUrl.trim()) { setUrlError('Enter an audio URL first.'); return }
    setUrlChecking(true)
    try {
      setUrlMeta(await api.validateAudioUrl(audioUrl.trim()))
    } catch (err) {
      setUrlError(err.message)
    } finally {
      setUrlChecking(false)
    }
  }

  // ---- processing ----------------------------------------------------------

  function resetOutcome() {
    setResult(null); setError(''); setStage('idle'); setFailedStage(null); setShowTranscript(false)
  }

  function stopPolling() {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null }
  }

  function startPolling(callId) {
    stopPolling()
    pollRef.current = setInterval(async () => {
      try {
        const status = await api.getCallStatus(callId)
        // Only move forward through pipeline states; the final POST response
        // is authoritative for COMPLETED / FAILED.
        if (['PROCESSING', 'TRANSCRIBING', 'ANALYZING'].includes(status.status)) setStage(status.status)
      } catch { /* transient poll failure: keep the last known stage */ }
    }, POLL_MS)
  }

  const audioReady = mode === 'upload' ? Boolean(file) : Boolean(urlMeta?.reachable)
  const canAnalyze = audioReady && leadId && callerId && !busy

  async function analyze() {
    if (!canAnalyze) return
    resetOutcome()
    let callId = null
    try {
      setStage('uploading'); setUploadPct(0)
      const created = mode === 'upload'
        ? await api.uploadCall({ file, leadId, callerId }, setUploadPct)
        : await api.createCallFromUrl({ audioUrl: audioUrl.trim(), leadId, callerId })
      callId = created.call_id
      setStage('UPLOADED')

      startPolling(callId)
      const processed = await api.processCall(callId)
      stopPolling()
      setStage('updating')
      setResult(processed)
      setStage('COMPLETED')
      onCompleted?.(processed)
    } catch (err) {
      stopPolling()
      setError(err.message)
      if (callId) {
        try {
          const status = await api.getCallStatus(callId)
          setFailedStage(status.failed_stage || status.status)
        } catch { /* ignore */ }
      } else {
        setFailedStage('uploading')
      }
      setStage('FAILED')
    }
  }

  function startAnother() {
    clearFile(); setAudioUrl(''); setUrlMeta(null); setUrlError('')
    resetOutcome()
  }

  // ---- render --------------------------------------------------------------

  const analysis = result?.analysis
  const intent = analysis ? intentBadge(analysis.customer_intent) : null
  const outcomeCls = { CONVERTED: 'converted', DROPPED: 'dropped', FOLLOW_UP_REQUIRED: 'upcoming' }[result?.outcome] || 'neutral'

  return (
    <section className="analyzer">
      <div className="analyzer-head">
        <div>
          <h2>Analyze New Call</h2>
          <p className="sub">
            Upload the recording of a call, link it to the lead and the BD who made it, and let the
            system decide the next action.
          </p>
        </div>
        {config && !config.vertex_configured && (
          <span className="badge due" title="GOOGLE_CLOUD_PROJECT is not set in backend/.env">
            Vertex AI not configured
          </span>
        )}
      </div>

      <div className="analyzer-grid">
        {/* ---------------- Audio input ---------------- */}
        <div className="panel">
          <div className="segmented" role="tablist">
            <button
              type="button" role="tab" aria-selected={mode === 'upload'}
              className={mode === 'upload' ? 'active' : ''}
              onClick={() => { setMode('upload'); resetOutcome() }}
              disabled={busy || uploadDisabled}
              title={uploadDisabled ? 'Uploads are disabled (AUDIO_STORAGE_MODE=url)' : undefined}
            >
              Upload Audio
            </button>
            <button
              type="button" role="tab" aria-selected={mode === 'url'}
              className={mode === 'url' ? 'active' : ''}
              onClick={() => { setMode('url'); resetOutcome() }}
              disabled={busy}
            >
              Audio URL
            </button>
          </div>

          {mode === 'upload' ? (
            <div className="audio-input">
              {!file ? (
                <label className={`dropzone ${busy ? 'disabled' : ''}`}>
                  <input
                    ref={fileInput} type="file" onChange={pickFile} disabled={busy}
                    accept={allowed.map((a) => '.' + a).join(',')}
                  />
                  <strong>Choose an audio file</strong>
                  <span className="sub">{allowed.map((a) => a.toUpperCase()).join(' · ')} — up to {maxMb} MB</span>
                </label>
              ) : (
                <div className="file-card">
                  <div className="file-icon">♫</div>
                  <div className="file-meta">
                    <div className="lead-name">{file.name}</div>
                    <div className="sub">
                      {formatBytes(file.size)}
                      {fileDuration != null && <> · {formatDuration(fileDuration)}</>}
                    </div>
                  </div>
                  <button type="button" className="btn small" onClick={clearFile} disabled={busy}>Remove</button>
                </div>
              )}
              {fileError && <div className="form-error">{fileError}</div>}
            </div>
          ) : (
            <div className="audio-input">
              <div className="url-row">
                <input
                  type="url" placeholder="https://example.com/call.mp3" value={audioUrl}
                  onChange={(e) => { setAudioUrl(e.target.value); setUrlMeta(null); setUrlError(''); resetOutcome() }}
                  disabled={busy} style={{ flex: 1 }}
                />
                <button type="button" className="btn" onClick={validateUrl} disabled={busy || urlChecking || !audioUrl.trim()}>
                  {urlChecking ? 'Checking…' : 'Validate Audio URL'}
                </button>
              </div>
              {urlError && <div className="form-error">{urlError}</div>}
              {urlMeta && (
                <div className="file-card ok">
                  <div className="file-icon">✓</div>
                  <div className="file-meta">
                    <div className="lead-name">{urlMeta.filename || 'Audio URL'}</div>
                    <div className="sub">
                      Reachable · {urlMeta.content_type}
                      {urlMeta.size_bytes != null && <> · {formatBytes(urlMeta.size_bytes)}</>}
                    </div>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        {/* ---------------- Association ---------------- */}
        <div className="panel">
          <div className="field">
            <label htmlFor="a-lead">Lead</label>
            <select id="a-lead" value={leadId} onChange={(e) => { setLeadId(e.target.value); resetOutcome() }} disabled={busy}>
              <option value="">Select lead…</option>
              {leads.map((l) => (
                <option key={l.lead_id} value={l.lead_id}>{l.name} — {l.lead_id}</option>
              ))}
            </select>
          </div>
          <div className="field" style={{ marginTop: 10 }}>
            <label htmlFor="a-caller">Caller / BD</label>
            <select id="a-caller" value={callerId} onChange={(e) => { setCallerId(e.target.value); resetOutcome() }} disabled={busy}>
              <option value="">Select caller…</option>
              {callers.map((c) => (
                <option key={c.caller_id} value={c.caller_id}>{c.name} — {c.caller_id}</option>
              ))}
            </select>
          </div>

          {(lead || caller) && (
            <div className="selection">
              {lead && (
                <div className="selection-card">
                  <div className="sub">Lead</div>
                  <div className="lead-name">{lead.name}</div>
                  <div>{lead.course}</div>
                  <div className="sub">{lead.phone} · {lead.lead_status.replaceAll('_', ' ')}</div>
                </div>
              )}
              {caller && (
                <div className="selection-card">
                  <div className="sub">Caller</div>
                  <div className="lead-name">{caller.name}</div>
                  <div>{caller.role}</div>
                  <div className="sub">{caller.caller_id}</div>
                </div>
              )}
            </div>
          )}

          <button type="button" className="btn primary analyze-btn" onClick={analyze} disabled={!canAnalyze}>
            {busy ? 'Analyzing…' : 'Analyze Call'}
          </button>
          {!busy && stage === 'idle' && !canAnalyze && (
            <div className="sub" style={{ marginTop: 6 }}>
              {!audioReady
                ? (mode === 'upload' ? 'Choose an audio file to continue.' : 'Validate the audio URL to continue.')
                : 'Select both a lead and a caller to continue.'}
            </div>
          )}
        </div>
      </div>

      {/* ---------------- Progress + result ---------------- */}
      {stage !== 'idle' && (
        <div className="panel outcome">
          <ProcessingSteps stage={stage} failedStage={failedStage} error={error} />
          {stage === 'uploading' && uploadPct > 0 && uploadPct < 100 && (
            <div className="sub">Uploading… {uploadPct}%</div>
          )}

          {stage === 'FAILED' && (
            <div className="result-actions">
              <button type="button" className="btn" onClick={analyze} disabled={!audioReady || !leadId || !callerId}>Retry</button>
              <button type="button" className="btn subtle" onClick={startAnother}>Start over</button>
            </div>
          )}

          {stage === 'COMPLETED' && result && (
            <div className="result">
              <div className="result-title">✓ Analysis completed</div>
              {result.degraded && (
                <div className="banner warn">
                  Gemini was unavailable, so the deterministic fallback analyzer produced this
                  result. Configure Vertex AI for AI analysis.
                </div>
              )}
              {!result.applied_to_lead && (
                <div className="banner warn">
                  The lead was not updated because a more recent call exists for it.
                </div>
              )}
              <dl className="kv result-kv">
                <dt>Outcome</dt>
                <dd><span className={`badge ${outcomeCls}`}>{outcomeLabel(result.outcome)}</span></dd>
                {analysis?.follow_up_required && (
                  <>
                    <dt>Follow-up</dt>
                    <dd>{formatFollowUp({ required: true, datetime: analysis.follow_up?.datetime })}</dd>
                    <dt>Reason</dt>
                    <dd>{analysis.follow_up?.reason || '—'}</dd>
                  </>
                )}
                <dt>Customer intent</dt>
                <dd><span className={`badge ${intent.cls}`}>{intent.label}</span></dd>
                <dt>Confidence</dt>
                <dd>{formatPercent(analysis?.confidence)} <span className="sub">· {analysis?.model}</span></dd>
                <dt>Summary</dt>
                <dd>{analysis?.summary || '—'}</dd>
                {analysis?.key_points?.length > 0 && (
                  <>
                    <dt>Key points</dt>
                    <dd><ul className="points">{analysis.key_points.map((p, i) => <li key={i}>{p}</li>)}</ul></dd>
                  </>
                )}
              </dl>

              {result.transcript && (
                <div style={{ marginTop: 10 }}>
                  <button type="button" className="btn small" onClick={() => setShowTranscript((v) => !v)}>
                    {showTranscript ? 'Hide transcript' : 'Show transcript'}
                  </button>
                  {showTranscript && <div className="transcript" style={{ marginTop: 8 }}>{result.transcript.transcript}</div>}
                </div>
              )}

              <div className="result-actions">
                <button type="button" className="btn" onClick={() => onOpenLead?.(result.lead_id)}>Open lead</button>
                <button type="button" className="btn subtle" onClick={startAnother}>Analyze another call</button>
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  )
}
