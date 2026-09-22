const CARDS = [
  { key: 'total_leads', label: 'Total Leads', cls: 'total', bucket: null },
  { key: 'follow_ups_required', label: 'Follow-ups Required', cls: 'upcoming', bucket: 'ALL' },
  { key: 'due_today', label: 'Due Today', cls: 'due', bucket: 'DUE_TODAY' },
  { key: 'overdue', label: 'Overdue', cls: 'overdue', bucket: 'OVERDUE' },
  { key: 'converted', label: 'Converted', cls: 'converted', bucket: null },
  { key: 'dropped', label: 'Dropped', cls: 'dropped', bucket: null },
]

export default function SummaryCards({ summary, activeBucket, onSelectBucket }) {
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
          >
            <div className="label">{card.label}</div>
            <div className="value">{summary[card.key] ?? 0}</div>
          </button>
        )
      })}
    </div>
  )
}
