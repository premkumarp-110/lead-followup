/** Small shared formatting helpers used by the tables, analyzer and modal. */

export function formatDateTime(value) {
  if (!value) return '—'
  return new Date(value).toLocaleString(undefined, {
    day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}

export function formatDate(value) {
  if (!value) return '—'
  return new Date(value).toLocaleDateString(undefined, {
    day: '2-digit', month: 'short', year: 'numeric',
  })
}

export function formatDuration(seconds) {
  if (seconds === null || seconds === undefined) return '—'
  const m = Math.floor(seconds / 60)
  const s = Math.round(seconds % 60)
  return `${m}m ${String(s).padStart(2, '0')}s`
}

export function formatPercent(value) {
  if (value === null || value === undefined) return '—'
  return `${Math.round(value * 100)}%`
}

/** Maps a follow-up bucket / outcome onto the badge classes in index.css. */
export function badgeFor(lead) {
  const bucket = lead.follow_up?.bucket
  if (bucket === 'OVERDUE') return { cls: 'overdue', label: 'Overdue' }
  if (bucket === 'DUE') return { cls: 'due', label: 'Due' }
  if (bucket === 'UPCOMING') return { cls: 'upcoming', label: 'Upcoming' }
  if (bucket === 'UNSCHEDULED') return { cls: 'unscheduled', label: 'Unscheduled' }
  if (bucket === 'COMPLETED') return { cls: 'converted', label: 'Completed' }
  if (bucket === 'CANCELLED') return { cls: 'dropped', label: 'Cancelled' }
  if (lead.lead_status === 'CONVERTED') return { cls: 'converted', label: 'Converted' }
  if (lead.lead_status === 'DROPPED') return { cls: 'dropped', label: 'Dropped' }
  return { cls: 'neutral', label: 'No follow-up' }
}

export function outcomeBadge(outcome, leadStatus) {
  if (outcome === 'CONVERTED') return { cls: 'converted', label: 'Converted' }
  if (outcome === 'DROPPED') return { cls: 'dropped', label: 'Dropped' }
  if (outcome === 'FOLLOW_UP_REQUIRED') return { cls: 'upcoming', label: 'Follow-up Required' }
  if (leadStatus === 'NEW') return { cls: 'neutral', label: 'Not analyzed' }
  return { cls: 'neutral', label: '—' }
}

export function outcomeLabel(outcome) {
  if (!outcome) return '—'
  return outcome.replaceAll('_', ' ').replace('FOLLOW UP', 'FOLLOW-UP')
}

export function intentBadge(intent) {
  const map = {
    READY_TO_ENROLL: { cls: 'converted', label: 'Ready to enroll' },
    INTERESTED: { cls: 'upcoming', label: 'Interested' },
    NEEDS_TIME: { cls: 'due', label: 'Needs time' },
    PRICE_SENSITIVE: { cls: 'due', label: 'Price sensitive' },
    NOT_INTERESTED: { cls: 'dropped', label: 'Not interested' },
    UNCLEAR: { cls: 'neutral', label: 'Unclear' },
  }
  return map[intent] || { cls: 'neutral', label: intent || '—' }
}

/** "23 Sep 2026, 11:00 AM" or the spec's no-date wording. */
/** How the lead sounded. Mirrors intentBadge, including its graceful tail. */
export function sentimentBadge(sentiment) {
  const label = sentiment?.label
  const map = {
    POSITIVE: { cls: 'converted', label: 'Positive' },
    NEUTRAL: { cls: 'neutral', label: 'Neutral' },
    NEGATIVE: { cls: 'dropped', label: 'Negative' },
    MIXED: { cls: 'due', label: 'Mixed' },
    UNKNOWN: { cls: 'neutral', label: 'Not assessed' },
  }
  return map[label] || { cls: 'neutral', label: 'Not assessed' }
}

/**
 * The arrow shown beside a sentiment.
 *
 * Only IMPROVED and DECLINED get a mark -- STABLE and UNKNOWN are the absence
 * of movement, and drawing something for them makes the two that matter harder
 * to spot.
 */
export function trajectoryMark(sentiment) {
  const map = {
    IMPROVED: { mark: '\u25b4', cls: 'traj-up', title: 'Warmed up over the call' },
    DECLINED: { mark: '\u25be', cls: 'traj-down', title: 'Cooled off over the call' },
  }
  return map[sentiment?.trajectory] || null
}

/** -0.45 -> "-0.45". Null when the tone was never judged. */
export function formatSentimentScore(sentiment) {
  const score = sentiment?.score
  if (score === null || score === undefined) return null
  return score > 0 ? `+${score.toFixed(2)}` : score.toFixed(2)
}

export function formatFollowUp(followUp) {
  if (!followUp?.required) return '—'
  if (!followUp.datetime) return 'Date not specified'
  return formatDateTime(followUp.datetime)
}

/**
 * Compact relative time: "2d ago", "in 3h", "just now".
 *
 * Shown NEXT TO the absolute time, never instead of it -- relative reads
 * urgency at a glance, absolute is what you trust and quote back to a lead.
 */
export function relativeToNow(value, now = Date.now()) {
  if (!value) return '—'
  const then = new Date(value).getTime()
  if (Number.isNaN(then)) return '—'

  const diff = then - now
  const abs = Math.abs(diff)
  const mins = Math.round(abs / 60000)
  const hours = Math.round(abs / 3600000)
  const days = Math.round(abs / 86400000)

  let magnitude
  if (mins < 1) return 'just now'
  if (mins < 60) magnitude = `${mins}m`
  else if (hours < 24) magnitude = `${hours}h`
  else if (days < 30) magnitude = `${days}d`
  else magnitude = `${Math.round(days / 30)}mo`

  return diff < 0 ? `${magnitude} ago` : `in ${magnitude}`
}

/** How late a follow-up is, phrased for a queue: "3d late". Empty when not late. */
export function overdueBy(value, now = Date.now()) {
  if (!value) return ''
  const then = new Date(value).getTime()
  if (Number.isNaN(then) || then >= now) return ''
  const mins = Math.round((now - then) / 60000)
  if (mins < 60) return `${mins}m late`
  const hours = Math.round(mins / 60)
  if (hours < 24) return `${hours}h late`
  return `${Math.round(hours / 24)}d late`
}

/** A tel: href, or null when there is no number -- never render a dead link. */

/**
 * Does this instant fall on today's IST date?
 *
 * Mirrors followup_service.is_ist_today on the backend. The business day is
 * Asia/Kolkata regardless of where the browser is, so "today" must be asked
 * in IST -- comparing in the browser's local zone would put anything after
 * 18:30 IST on the wrong day for a user outside India.
 */
export function isIstToday(value, now = new Date()) {
  if (!value) return false
  const then = new Date(value)
  if (Number.isNaN(then.getTime())) return false
  const IST_MS = (5 * 60 + 30) * 60000
  const istDay = (d) => Math.floor((d.getTime() + IST_MS) / 86400000)
  return istDay(then) === istDay(now)
}

/** A CRM stage's ladder step: "DNP 5" -> 5, "Follow-up 1" -> 1, "New" -> null.
 *  The numbered ladders encode attempt count, so a DNP 5 lead is a different
 *  proposition from a DNP 1 and the UI should not flatten them. */
export function stageStep(stage) {
  const m = /(\d+)\s*$/.exec(String(stage || ''))
  return m ? Number(m[1]) : null
}

/** Products the CRM uses as "nothing recorded" sentinels, not real courses. */
const SENTINEL_PRODUCTS = new Set(['common', 'do-not-know', 'career_consultation'])

export function formatProduct(product) {
  if (!product) return '—'
  return SENTINEL_PRODUCTS.has(product) ? '—' : product
}

/** Every CRM field is nullable; render a dash, never "null"/"undefined". */
export function orDash(value) {
  if (value === null || value === undefined || value === '') return '—'
  return String(value)
}

/** A short, stable label for a lead. There is no name/phone/email upstream. */
export function leadLabel(lead) {
  return lead?.lead_id || '—'
}

export function formatScore(value) {
  if (value === null || value === undefined) return '—'
  return `${Math.round(value)}%`
}
