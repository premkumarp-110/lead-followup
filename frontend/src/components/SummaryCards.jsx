/**
 * The summary row.
 *
 * `upcoming` and `unscheduled` have been in DashboardSummary since the first
 * commit but were never rendered -- two counts the backend computes that
 * nobody could see. They are cards now.
 *
 * Note these do NOT partition: due_today overlaps overdue and upcoming by
 * design (it answers "what do I do today"), so the sub-labels say what each
 * one counts rather than inviting anyone to add them up.
 */
const CARDS = [
  { key: 'total_leads', label: 'Total Leads', cls: 'total', bucket: null, sub: 'matching filters' },
  { key: 'follow_ups_required', label: 'Pending', cls: 'upcoming', bucket: 'ALL', sub: 'follow-ups open' },
  { key: 'overdue', label: 'Overdue', cls: 'overdue', bucket: 'OVERDUE', sub: 'past their time' },
  { key: 'due_today', label: 'Due Today', cls: 'due', bucket: 'DUE_TODAY', sub: 'incl. overdue today' },
  { key: 'upcoming', label: 'Upcoming', cls: 'upcoming', bucket: 'UPCOMING', sub: 'later than today' },
  { key: 'unscheduled', label: 'No Date', cls: 'unscheduled', bucket: 'UNSCHEDULED', sub: 'needs a time set' },
  { key: 'converted', label: 'Converted', cls: 'converted', bucket: 'CONVERTED', sub: 'closed won' },
  { key: 'dropped', label: 'Dropped', cls: 'dropped', bucket: 'DROPPED', sub: 'closed lost' },
]

export default function SummaryCards({ summary, activeBucket, onSelectBucket, loading }) {
  if (loading && !summary) {
    return (
      <div className="cards">
        {CARDS.map((c) => (
          <div className="card" key={c.key}>
            <div className="skeleton skeleton-line" style={{ width: '60%' }} />
            <div className="skeleton skeleton-line" style={{ width: 40, height: 22 }} />
          </div>
        ))}
      </div>
    )
  }
  if (!summary) return null

  return (
    <div className="cards">
      {CARDS.map((card) => {
        const clickable = Boolean(card.bucket)
        const active = clickable && activeBucket === card.bucket
        const classes = ['card', card.cls, clickable ? 'clickable' : '', active ? 'active' : '']
        return (
          <button
            key={card.key}
            type="button"
            className={classes.filter(Boolean).join(' ')}
            onClick={clickable ? () => onSelectBucket(active ? 'ALL' : card.bucket) : undefined}
            disabled={!clickable}
            aria-pressed={clickable ? active : undefined}
          >
            <div className="label">{card.label}</div>
            <div className="value">{summary[card.key] ?? 0}</div>
            <div className="sub">{card.sub}</div>
          </button>
        )
      })}
    </div>
  )
}
