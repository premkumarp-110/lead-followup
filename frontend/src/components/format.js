/** Small shared formatting helpers used by the tables and the modal. */

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
  const s = seconds % 60
  return `${m}m ${String(s).padStart(2, '0')}s`
}

/** Maps a follow-up bucket / outcome onto the badge classes in index.css. */
export function badgeFor(lead) {
  const bucket = lead.follow_up?.bucket
  if (bucket === 'OVERDUE') return { cls: 'overdue', label: 'Overdue' }
  if (bucket === 'DUE') return { cls: 'due', label: 'Due' }
  if (bucket === 'UPCOMING') return { cls: 'upcoming', label: 'Upcoming' }
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
  if (leadStatus === 'NEW') return { cls: 'neutral', label: 'Not processed' }
  return { cls: 'neutral', label: '—' }
}
