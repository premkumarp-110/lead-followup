/**
 * The spec S32 progress stepper.
 *
 * `stage` is one of: idle | uploading | UPLOADED | PROCESSING | TRANSCRIBING |
 * ANALYZING | updating | COMPLETED | FAILED. Every value except the first and
 * last two comes straight from the backend's call status, so this reflects
 * real pipeline state rather than a timer.
 *
 * `mode` only changes labels/wording -- a text-mode call still passes through
 * the same backend states (TRANSCRIBING just resolves instantly since the
 * transcript was already provided).
 */
function stepsFor(mode) {
  const isText = mode === 'text'
  return [
    { key: 'upload', label: isText ? 'Transcript received' : 'Audio uploaded', reached: ['UPLOADED', 'PROCESSING', 'TRANSCRIBING', 'ANALYZING', 'updating', 'COMPLETED'], active: ['uploading'] },
    { key: 'transcribe', label: isText ? 'Transcript ready' : 'Transcript generated', reached: ['ANALYZING', 'updating', 'COMPLETED'], active: ['PROCESSING', 'TRANSCRIBING'] },
    { key: 'analyze', label: 'Conversation analyzed', reached: ['updating', 'COMPLETED'], active: ['ANALYZING'] },
    { key: 'update', label: 'Lead updated', reached: ['COMPLETED'], active: ['updating'] },
    { key: 'done', label: 'Completed', reached: ['COMPLETED'], active: [] },
  ]
}

function activeTextFor(mode) {
  return {
    uploading: mode === 'text' ? 'Submitting transcript…' : 'Uploading audio…',
    UPLOADED: 'Starting…',
    PROCESSING: 'Preparing audio…',
    TRANSCRIBING: 'Transcribing audio…',
    ANALYZING: 'Analyzing conversation…',
    updating: 'Updating lead…',
  }
}

export default function ProcessingSteps({ stage, failedStage, error, mode }) {
  if (stage === 'idle') return null

  const STEPS = stepsFor(mode)
  const ACTIVE_TEXT = activeTextFor(mode)

  // When FAILED, the step matching the failed backend stage is marked failed
  // and everything before it stays checked.
  let failedIndex = -1
  if (stage === 'FAILED') {
    failedIndex = STEPS.findIndex((s) => s.active.includes(failedStage))
    // Unknown stage (e.g. an older backend without `failed_stage`): the
    // upload succeeded if we got a call id, so blame the first pipeline step.
    if (failedIndex === -1) failedIndex = failedStage === 'uploading' ? 0 : 1
  }

  return (
    <ol className="steps" aria-live="polite">
      {STEPS.map((step, index) => {
        let state = 'pending'
        if (stage === 'FAILED') {
          if (index < failedIndex) state = 'done'
          else if (index === failedIndex) state = 'failed'
        } else if (step.reached.includes(stage)) state = 'done'
        else if (step.active.includes(stage)) state = 'active'

        const icon = { done: '✓', active: '⏳', failed: '✕', pending: '○' }[state]
        const text = state === 'active' ? (ACTIVE_TEXT[stage] || step.label) : step.label
        return (
          <li key={step.key} className={`step ${state}`}>
            <span className="step-icon">{icon}</span>
            <span>{text}</span>
            {state === 'failed' && error && <div className="step-error">{error}</div>}
          </li>
        )
      })}
    </ol>
  )
}
