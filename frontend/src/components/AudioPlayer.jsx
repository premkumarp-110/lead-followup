import { useState } from 'react'
import api from '../services/api.js'

/**
 * Plays a call recording through the backend (never a filesystem path).
 * Renders nothing when playback is disabled by AUDIO_PLAYBACK_ENABLED=false.
 */
export default function AudioPlayer({ call, config }) {
  const [failed, setFailed] = useState(false)
  if (!config?.audio_playback_enabled || !call) return null

  const hasAudio = call.source_type === 'URL' ? Boolean(call.audio_url) : Boolean(call.audio_filename)
  if (!hasAudio) {
    return <p className="sub">Recording not available for this call{call.seeded ? ' (seeded sample)' : ''}.</p>
  }
  if (failed) {
    return <p className="sub">The recording could not be loaded for playback.</p>
  }

  return (
    <div className="audio-player">
      <audio controls preload="none" src={api.audioUrlFor(call.call_id)} onError={() => setFailed(true)}>
        Your browser does not support audio playback.
      </audio>
      <div className="sub">
        {call.source_type === 'URL' ? 'Streaming from the supplied URL' : `Stored recording · ${call.audio_filename}`}
      </div>
    </div>
  )
}
