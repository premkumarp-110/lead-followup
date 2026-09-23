import { useEffect, useState } from 'react'
import BDSelector from './BDSelector.jsx'

// The first four narrow the active worklist; Unscheduled is the "needs a date"
// slice of it; Converted / Dropped switch to the closed tab.
const QUICK_FILTERS = [
  { value: 'ALL', label: 'All' },
  { value: 'DUE_TODAY', label: 'Due Today' },
  { value: 'OVERDUE', label: 'Overdue' },
  { value: 'UPCOMING', label: 'Upcoming' },
  { value: 'UNSCHEDULED', label: 'Unscheduled' },
  { value: 'CONVERTED', label: 'Converted' },
  { value: 'DROPPED', label: 'Dropped' },
]

const LABELS = {
  search: 'Search', bd: 'BD', product: 'Product', stage: 'Stage',
  language: 'Language', state: 'State', lead_source: 'Source',
  segmentation: 'Segment', last_disposition_status: 'Disposition',
  connected_min: 'Min connected', attempts_min: 'Min attempts',
  win_probability_min: 'Min win %',
  outcome: 'Outcome', status: 'Status', date: 'On',
  date_from: 'From', date_to: 'To', bucket: 'View',
}

/** Sentinel products the CRM uses for "nothing recorded" -- not real courses,
 *  so they must not sit in the dropdown as though they were. */
const SENTINEL_PRODUCTS = new Set(['common', 'do-not-know', 'career_consultation'])

const pretty = (v) => String(v).replaceAll('_', ' ')

/** The bd filter carries a caller_id; show the human name on the chip. */
function bdLabel(bds, value) {
  return (bds || []).find((b) => b.caller_id === value)?.name || value
}

export default function FilterBar({ filters, options, resultCount, onChange, onReset }) {
  // Typing straight into `filters` would fire a request per keystroke.
  const [search, setSearch] = useState(filters.search)
  useEffect(() => { setSearch(filters.search) }, [filters.search])
  useEffect(() => {
    if (search === filters.search) return
    const t = setTimeout(() => onChange({ ...filters, search }), 300)
    return () => clearTimeout(t)
  }, [search]) // eslint-disable-line react-hooks/exhaustive-deps

  const set = (key) => (event) => onChange({ ...filters, [key]: event.target.value })

  // A range that cannot match anything is worth saying before the request.
  const badRange = Boolean(filters.date_from && filters.date_to && filters.date_from > filters.date_to)

  const active = Object.entries(filters).filter(([key, value]) => {
    if (!value) return false
    if (key === 'bucket') return value !== 'ALL'
    return true
  })

  const noOptions = !options.bds?.length && !options.products?.length

  return (
    <div className="filter-bar">
      <div className="filter-row">
        <div className="field" style={{ flex: '1 1 260px' }}>
          <label htmlFor="f-search">Search Lead</label>
          <input
            id="f-search"
            placeholder="Lead id or external id"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{ width: '100%' }}
          />
        </div>

        {/* Searchable: there are 100+ owners in the real CRM and duplicate
            first names, so a plain <select> is unusable and a name is not an
            identity. The value is the caller_id; the backend also accepts the
            email. */}
        <div className="field" style={{ flex: '0 1 240px' }}>
          <label htmlFor="f-bd">BD</label>
          <BDSelector id="f-bd" callers={options.bds} value={filters.bd}
                      onChange={(v) => onChange({ ...filters, bd: v || '' })} />
        </div>

        <div className="field">
          <label htmlFor="f-product">Product</label>
          <select id="f-product" value={filters.product} onChange={set('product')}
                  disabled={!options.products?.length}>
            <option value="">All products</option>
            {options.products?.filter((c) => !SENTINEL_PRODUCTS.has(c))
              .map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </div>

        <div className="field">
          <label htmlFor="f-stage">Stage</label>
          <select id="f-stage" value={filters.stage} onChange={set('stage')}
                  disabled={!options.stages?.length}>
            <option value="">All stages</option>
            {options.stages?.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </div>

        <div className="field">
          <label htmlFor="f-outcome">Outcome</label>
          <select id="f-outcome" value={filters.outcome} onChange={set('outcome')}>
            <option value="">All outcomes</option>
            {options.outcomes?.map((o) => <option key={o} value={o}>{pretty(o)}</option>)}
          </select>
        </div>

        <div className="field">
          <label htmlFor="f-status">Follow-up Status</label>
          <select id="f-status" value={filters.status} onChange={set('status')}>
            <option value="">All statuses</option>
            {options.follow_up_statuses?.map((s) => <option key={s} value={s}>{pretty(s)}</option>)}
          </select>
        </div>
      </div>

      <div className="filter-row">
        <div className="field">
          <label htmlFor="f-language">Language</label>
          <select id="f-language" value={filters.language} onChange={set('language')}
                  disabled={!options.languages?.length}>
            <option value="">Any language</option>
            {options.languages?.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </div>

        <div className="field">
          <label htmlFor="f-source">Source</label>
          <select id="f-source" value={filters.lead_source} onChange={set('lead_source')}
                  disabled={!options.lead_sources?.length}>
            <option value="">Any source</option>
            {options.lead_sources?.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </div>

        <div className="field">
          <label htmlFor="f-state">State</label>
          <select id="f-state" value={filters.state} onChange={set('state')}
                  disabled={!options.states?.length}>
            <option value="">Any state</option>
            {options.states?.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </div>

        <div className="field">
          <label htmlFor="f-disp">Disposition</label>
          <select id="f-disp" value={filters.last_disposition_status}
                  onChange={set('last_disposition_status')} disabled={!options.dispositions?.length}>
            <option value="">Any disposition</option>
            {options.dispositions?.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </div>

        <div className="field">
          <label htmlFor="f-sentiment">Sentiment</label>
          <select id="f-sentiment" value={filters.sentiment} onChange={set('sentiment')}
                  disabled={!options.sentiments?.length}>
            <option value="">Any sentiment</option>
            {options.sentiments?.map((c) => <option key={c} value={c}>{pretty(c)}</option>)}
          </select>
        </div>

        <div className="field" style={{ maxWidth: 130 }}>
          <label htmlFor="f-connected">Min connected</label>
          <input id="f-connected" type="number" min="0" value={filters.connected_min}
                 onChange={set('connected_min')} placeholder="0" />
        </div>

        <div className="field" style={{ maxWidth: 120 }}>
          <label htmlFor="f-win">Min win %</label>
          <input id="f-win" type="number" min="0" max="100" value={filters.win_probability_min}
                 onChange={set('win_probability_min')} placeholder="0" />
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
      </div>

      {badRange && (
        <div className="banner warn" style={{ margin: '12px 0 0' }}>
          “From” is after “To”, so nothing can match. Swap the dates or clear one.
        </div>
      )}

      {noOptions && (
        <div className="banner info" style={{ margin: '12px 0 0' }}>
          No leads exist yet, so there is nothing to filter. Seed the database first.
        </div>
      )}

      {/* What is actually narrowing the list, visible without opening a dropdown. */}
      {active.length > 0 && (
        <div className="filter-row" style={{ alignItems: 'center' }}>
          <span className="sub">
            {resultCount === null ? 'Filtering' : `${resultCount} result${resultCount === 1 ? '' : 's'}`}
            {' · '}filtered by
          </span>
          <div className="chips">
            {active.map(([key, value]) => (
              <button
                key={key}
                type="button"
                className="chip active"
                title={`Remove the ${LABELS[key] || key} filter`}
                onClick={() => onChange({ ...filters, [key]: key === 'bucket' ? 'ALL' : '' })}
              >
                {LABELS[key] || key}: {key === 'bd' ? bdLabel(options.bds, value) : pretty(value)} ×
              </button>
            ))}
          </div>
          <button type="button" className="btn small subtle" onClick={onReset}>Clear all</button>
        </div>
      )}
    </div>
  )
}
