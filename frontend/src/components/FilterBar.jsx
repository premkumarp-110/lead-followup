const QUICK_FILTERS = [
  { value: 'ALL', label: 'All' },
  { value: 'DUE_TODAY', label: 'Today' },
  { value: 'OVERDUE', label: 'Overdue' },
  { value: 'UPCOMING', label: 'Upcoming' },
]

export default function FilterBar({ filters, options, onChange, onReset }) {
  const set = (key) => (event) => onChange({ ...filters, [key]: event.target.value })

  return (
    <div className="filter-bar">
      <div className="filter-row">
        <div className="field" style={{ flex: '1 1 240px' }}>
          <label htmlFor="f-search">Search Lead</label>
          <input
            id="f-search"
            placeholder="Name, email, phone or lead id"
            value={filters.search}
            onChange={set('search')}
            style={{ width: '100%' }}
          />
        </div>

        <div className="field">
          <label htmlFor="f-bd">BD</label>
          <select id="f-bd" value={filters.bd} onChange={set('bd')}>
            <option value="">All BDs</option>
            {options.bds?.map((bd) => <option key={bd} value={bd}>{bd}</option>)}
          </select>
        </div>

        <div className="field">
          <label htmlFor="f-course">Course</label>
          <select id="f-course" value={filters.course} onChange={set('course')}>
            <option value="">All courses</option>
            {options.courses?.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </div>

        <div className="field">
          <label htmlFor="f-outcome">Outcome</label>
          <select id="f-outcome" value={filters.outcome} onChange={set('outcome')}>
            <option value="">All outcomes</option>
            {options.outcomes?.map((o) => (
              <option key={o} value={o}>{o.replaceAll('_', ' ')}</option>
            ))}
          </select>
        </div>

        <div className="field">
          <label htmlFor="f-status">Follow-up Status</label>
          <select id="f-status" value={filters.status} onChange={set('status')}>
            <option value="">All statuses</option>
            {options.follow_up_statuses?.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>
      </div>

      <div className="filter-row">
        <div className="field">
          <label htmlFor="f-date">Follow-up Date</label>
          <input id="f-date" type="date" value={filters.date} onChange={set('date')} />
        </div>
        <div className="field">
          <label htmlFor="f-from">From</label>
          <input id="f-from" type="date" value={filters.date_from} onChange={set('date_from')} />
        </div>
        <div className="field">
          <label htmlFor="f-to">To</label>
          <input id="f-to" type="date" value={filters.date_to} onChange={set('date_to')} />
        </div>

        <div className="field" style={{ marginLeft: 'auto' }}>
          <label>Quick filter</label>
          <div className="chips">
            {QUICK_FILTERS.map((q) => (
              <button
                key={q.value}
                type="button"
                className={`chip ${filters.bucket === q.value ? 'active' : ''}`}
                onClick={() => onChange({ ...filters, bucket: q.value })}
              >
                {q.label}
              </button>
            ))}
          </div>
        </div>

        <button type="button" className="btn subtle" onClick={onReset}>Clear filters</button>
      </div>
    </div>
  )
}
