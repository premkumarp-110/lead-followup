import axios from 'axios'

const baseURL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

const client = axios.create({ baseURL, timeout: 15000 })
// Transcription + analysis of a several-minute recording can take a while.
const longClient = axios.create({ baseURL, timeout: 300000 })

/** Turns an axios failure into a readable message for the UI banner. */
function describeError(error) {
  if (error.response) {
    const detail = error.response.data?.detail
    if (Array.isArray(detail)) return detail.map((d) => d.msg).join('; ')
    if (typeof detail === 'string') return detail
    return `Request failed (${error.response.status})`
  }
  if (error.code === 'ECONNABORTED') return 'The request timed out. The backend may still be processing.'
  return `Cannot reach the API at ${baseURL}. Is the backend running?`
}

async function request(promise) {
  try {
    const { data } = await promise
    return data
  } catch (error) {
    throw new Error(describeError(error))
  }
}

const get = (path, params, c = client) => request(c.get(path, { params }))

/** Drops empty values so we never send `?bd=` and match nothing. */
export function cleanParams(filters) {
  return Object.fromEntries(
    Object.entries(filters).filter(([, v]) => v !== '' && v !== null && v !== undefined),
  )
}

export const api = {
  baseURL,

  // ---- config / meta ----
  getConfig: () => get('/api/config'),

  // ---- dashboard ----
  getSummary: (filters = {}) => get('/api/dashboard/summary', cleanParams(filters)),
  getFilterOptions: () => get('/api/dashboard/filters'),
  getFollowUps: (filters = {}) => get('/api/leads/follow-ups', cleanParams(filters)),
  getClosed: (filters = {}) => get('/api/leads/closed', cleanParams(filters)),
  getLeads: (filters = {}) => get('/api/leads', cleanParams(filters)),
  getLead: (leadId) => get(`/api/leads/${leadId}`),
  getCallers: () => get('/api/callers'),

  // ---- call ingestion ----
  uploadCall({ file, leadId, callerId }, onProgress) {
    const form = new FormData()
    form.append('file', file)
    form.append('lead_id', leadId)
    form.append('caller_id', callerId)
    return request(
      longClient.post('/api/calls/upload', form, {
        onUploadProgress: (e) => {
          if (onProgress && e.total) onProgress(Math.round((e.loaded / e.total) * 100))
        },
      }),
    )
  },
  validateAudioUrl: (audioUrl) => request(client.post('/api/calls/validate-url', { audio_url: audioUrl })),
  createCallFromUrl: ({ audioUrl, leadId, callerId }) =>
    request(client.post('/api/calls/from-url', { audio_url: audioUrl, lead_id: leadId, caller_id: callerId })),
  createCallFromText: ({ transcript, leadId, callerId }) =>
    request(client.post('/api/calls/from-text', { transcript, lead_id: leadId, caller_id: callerId })),

  // ---- processing ----
  processCall: (callId) => request(longClient.post(`/api/calls/${callId}/process`)),
  getCallStatus: (callId) => get(`/api/calls/${callId}/status`),
  getCallAnalyses: (callId) => get(`/api/calls/${callId}/analyses`),
  /** URL the <audio> element streams from. The backend resolves the file by call id. */
  audioUrlFor: (callId) => `${baseURL}/api/calls/${callId}/audio`,

  // ---- follow-up actions ----
  updateFollowUp: (leadId, payload) => request(client.patch(`/api/leads/${leadId}/follow-up`, payload)),
}

export default api
