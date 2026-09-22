import axios from 'axios'

const baseURL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

const client = axios.create({ baseURL, timeout: 10000 })

/** Turns an axios failure into a readable message for the UI banner. */
function describeError(error) {
  if (error.response) {
    const detail = error.response.data?.detail
    if (Array.isArray(detail)) return detail.map((d) => d.msg).join('; ')
    if (typeof detail === 'string') return detail
    return `Request failed (${error.response.status})`
  }
  return `Cannot reach the API at ${baseURL}. Is the backend running?`
}

async function get(path, params) {
  try {
    const { data } = await client.get(path, { params })
    return data
  } catch (error) {
    throw new Error(describeError(error))
  }
}

/** Drops empty values so we never send `?bd=` and match nothing. */
export function cleanParams(filters) {
  return Object.fromEntries(
    Object.entries(filters).filter(([, v]) => v !== '' && v !== null && v !== undefined),
  )
}

export const api = {
  baseURL,
  getSummary: (filters = {}) => get('/api/dashboard/summary', cleanParams(filters)),
  getFilterOptions: () => get('/api/dashboard/filters'),
  getFollowUps: (filters = {}) => get('/api/leads/follow-ups', cleanParams(filters)),
  getNonFollowUps: (filters = {}) => get('/api/leads/non-follow-ups', cleanParams(filters)),
  getLead: (leadId) => get(`/api/leads/${leadId}`),

  async processCall(callId) {
    try {
      const { data } = await client.post(`/api/calls/${callId}/process`)
      return data
    } catch (error) {
      throw new Error(describeError(error))
    }
  },

  async updateFollowUp(leadId, payload) {
    try {
      const { data } = await client.patch(`/api/leads/${leadId}/follow-up`, payload)
      return data
    } catch (error) {
      throw new Error(describeError(error))
    }
  },
}

export default api
