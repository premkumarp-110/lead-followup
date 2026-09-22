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

export function formatTime(value) {
  if (!value) return '—'
  return new Date(value).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
}

export function formatDuration(seconds) {
  if (seconds === null || seconds === undefined) return '—'
  const m = Math.floor(seconds / 60)
  const s = Math.round(seconds % 60)
  return `${m}m ${String(s).padStart(2, '0')}s`
}

export function formatBytes(bytes) {
  if (bytes === null || bytes === undefined) return '—'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
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
export function formatFollowUp(followUp) {
  if (!followUp?.required) return '—'
  if (!followUp.datetime) return 'Date not specified'
  return formatDateTime(followUp.datetime)
}
