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
  getLead: (leadId) => get(`/api/leads/${leadId}`),
  /**
   * `includeInactive` defaults true: an inactive BD can still own pending
   * follow-ups, and excluding them makes those queues unreachable from the
   * selector. The combobox marks them instead of hiding them.
   */
  getCallers: (includeInactive = true) =>
    get('/api/callers', cleanParams({ include_inactive: includeInactive || undefined })),

  // ---- calls ----
  getCalls: (leadId) => get('/api/calls', cleanParams({ lead_id: leadId })),
  getCallTranscript: (callId) => get(`/api/calls/${callId}/transcript`),
  /**
   * Analyse one existing call. Long client: this runs transcription and the
   * analysis model synchronously and can take minutes on a long recording.
   */
  analyzeCall: (callId) => request(longClient.post(`/api/calls/${callId}/analyze`)),
  /** URL the <audio> element streams from. The backend proxies it from the CRM. */
  audioUrlFor: (callId) => `${baseURL}/api/calls/${callId}/audio`,

  // ---- follow-up actions ----
  updateFollowUp: (leadId, payload) => request(client.patch(`/api/leads/${leadId}/follow-up`, payload)),

  // ---- reminders ----
  /** `bd` is a caller_id, not a name -- reminders address a mailbox. */
  getPendingAlerts: (bd) => get('/api/alerts/pending', cleanParams({ bd })),
  getAlertHistory: (bd, limit = 20) => get('/api/alerts/history', cleanParams({ bd, limit })),
  /** Sending to every BD makes one blocking SMTP call per BD, so this uses the long client. */
  sendDigest: (bdId) => request(longClient.post('/api/alerts/send', bdId ? { bd_id: bdId } : {})),
  /**
   * Remind the owner about ONE lead, now. Works for a follow-up that is merely
   * upcoming or has no date -- neither of which the daily digest includes.
   * 409 when there is genuinely nothing to remind about.
   */
  sendLeadReminder: (leadId) => request(longClient.post('/api/alerts/send', { lead_id: leadId })),
  /** The one-lead reminder HTML, without sending it. Needs no SMTP. */
  leadReminderPreviewUrl: (leadId) =>
    `${baseURL}/api/alerts/preview?lead=${encodeURIComponent(leadId)}`,

  // ---- insights ----
  getInsightsOverview: (filters = {}) => get('/api/insights/overview', cleanParams(filters)),
  getInsightsByBD: (filters = {}) => get('/api/insights/by-bd', cleanParams(filters)),
}

export default api
