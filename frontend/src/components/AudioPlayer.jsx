import { useState } from 'react'
import api from '../services/api.js'

/**
 * Plays a call recording.
 *
 * The audio is proxied by our backend, never streamed from the CRM directly:
 * a browser cannot attach the CRM's `X-API-Key` header to an <audio src>, so
 * pointing the tag upstream returns 401. The backend fetches it once with the
 * key, caches it, and serves it from there -- which also restores duration and
 * seeking, since the CRM sends no content-length and ignores Range.
 *
 * Renders nothing when playback is disabled by AUDIO_PLAYBACK_ENABLED=false.
 */
export default function AudioPlayer({ call, config }) {
  const [failed, setFailed] = useState(false)
  if (!config?.audio_playback_enabled || !call) return null

  if (!call.has_recording) {
    return <p className="sub">No recording exists for this call.</p>
  }

  // The key is an operator setting, so say which problem this is rather than
  // letting the player fail silently.
  if (config.recording_source_configured === false) {
    return (
      <p className="sub">
        Recording playback is unavailable: LEAD_CALL_API_KEY is not configured on the server.
      </p>
    )
  }

  if (failed) {
    return (
      <p className="sub">
        The recording could not be fetched from the CRM. It may have been removed upstream,
        or the API key may have been revoked.
      </p>
    )
  }

  return (
    <div className="audio-player">
      {/* preload="none": the first play triggers a CRM fetch that can take
          several seconds, so do not pay it for every call on the page. */}
      <audio controls preload="none" src={api.audioUrlFor(call.call_id)} onError={() => setFailed(true)}>
        Your browser does not support audio playback.
      </audio>
      <div className="sub">Streamed from the CRM and cached on first play.</div>
    </div>
  )
}
