import { useCallback, useEffect, useMemo, useState } from 'react'
import api from '../services/api.js'
import { csvName, downloadCsv } from '../services/csv.js'
import { formatDateTime, relativeToNow } from './format.js'

const BD_CSV = [
  { key: 'bd_id', label: 'BD ID' }, { key: 'bd_name', label: 'BD' },
  { key: 'bd_email', label: 'Email' }, { key: 'assigned', label: 'Assigned' },
  { key: 'overdue', label: 'Overdue' }, { key: 'due', label: 'Due' },
  { key: 'due_today', label: 'Due Today' }, { key: 'upcoming', label: 'Upcoming' },
  { key: 'unscheduled', label: 'No Date' },
  { key: 'rescheduled_all_time', label: 'Rescheduled (all time)' },
  { key: 'converted', label: 'Converted' }, { key: 'dropped', label: 'Dropped' },
]

const SORTABLE = [
  ['bd_name', 'BD', false], ['assigned', 'Assigned', true], ['overdue', 'Overdue', true],
  ['due_today', 'Due today', true], ['upcoming', 'Upcoming', true], ['unscheduled', 'No date', true],
  ['rescheduled_all_time', 'Rescheduled', true], ['converted', 'Converted', true], ['dropped', 'Dropped', true],
]

/**
 * Queue health for whoever is asking "is the team keeping up?".
 *
 * Every figure here comes from /api/insights, which applies BOTH halves of the
 * shared filter contract against one `now` -- so these numbers reconcile with
 * the Leads table by construction rather than by coincidence.
 *
 * Deliberately absent: trends. Nothing stores the daily state of the queue, so
 * week-over-week deltas are not derivable. Saying so beats drawing a fake arrow.
 */
export default function InsightsView({ dataVersion }) {
  const [overview, setOverview] = useState(null)
  const [byBd, setByBd] = useState(null)
  const [history, setHistory] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [sort, setSort] = useState({ key: 'overdue', dir: 'desc' })

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [o, b] = await Promise.all([api.getInsightsOverview(), api.getInsightsByBD()])
      setOverview(o)
      setByBd(b)
      api.getAlertHistory(null, 5).then(setHistory).catch(() => setHistory([]))
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load, dataVersion])

  const rows = useMemo(() => {
    if (!byBd?.rows) return []
    const { key, dir } = sort
    return [...byBd.rows].sort((a, b) => {
      const av = a[key], bv = b[key]
      const cmp = typeof av === 'string' ? av.localeCompare(bv) : (av ?? 0) - (bv ?? 0)
      return dir === 'asc' ? cmp : -cmp
    })
  }, [byBd, sort])

  if (loading && !overview) return <InsightsSkeleton />

  if (error) {
    return (
      <div className="panel">
        <div className="empty">
          <div className="empty-title">Could not load insights</div>
          <div className="empty-hint">{error}</div>
          <div className="empty-actions"><button className="btn" onClick={load}>Try again</button></div>
        </div>
      </div>
    )
  }

  const empty = overview.total_leads === 0
  const mix = overview.outcome_mix
  const tone = overview.sentiment_mix || {}
  const maxAge = Math.max(1, ...overview.ageing.map((a) => a.count))

  function toggleSort(key) {
    setSort((s) => (s.key === key ? { key, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { key, dir: 'desc' }))
  }

  return (
    <>
      {empty && (
        <div className="banner info">
          No leads in the database yet, so every figure below is zero. Seed the database or
          analyze a call to populate it.
        </div>
      )}

      <div className="cards">
        <Kpi label="Pending" value={overview.pending_total} sub="follow-ups open" cls="upcoming" />
        <Kpi label="Overdue" value={overview.overdue_total} sub="past their time" cls="overdue" />
        <Kpi label="Due Today" value={overview.due_today_total} sub="overlaps overdue" cls="due" />
        <Kpi label="No Date" value={overview.unscheduled_total} sub="needs a time set" cls="unscheduled" />
        <Kpi label="Total Leads" value={overview.total_leads} sub="matching filters" cls="total" />
      </div>

      <div className="insights-grid">
        <section className="panel">
          <div className="panel-head">
            <h3>How late is the backlog?</h3>
            <span className="hint">{overview.overdue_total} overdue</span>
          </div>
          {overview.overdue_total === 0 ? (
            <div className="empty">
              <div className="empty-title">Nothing is overdue</div>
              <div className="empty-hint">Every pending follow-up is still within its time.</div>
            </div>
          ) : (
            <div className="hist">
              {overview.ageing.map((band) => (
                <div className="hist-row" key={band.key}>
                  <span className="hist-label">{band.label}</span>
                  <svg className="hist-bar" viewBox="0 0 100 10" preserveAspectRatio="none" role="img"
                    aria-label={`${band.label}: ${band.count}`}>
                    <rect x="0" y="0" width="100" height="10" rx="1" fill="var(--surface-3)" />
                    <rect x="0" y="0" width={Math.max(band.count ? 2 : 0, (band.count / maxAge) * 100)}
                      height="10" rx="1" fill="var(--overdue-dot)" />
                  </svg>
                  <span className="hist-value">{band.count}</span>
                </div>
              ))}
            </div>
          )}
          {/* The bands sum to overdue; stating it stops anyone double-counting. */}
          <p className="sub" style={{ marginTop: 12, marginBottom: 0 }}>
            Bands are exclusive and sum to the overdue total.
          </p>
        </section>

        <section className="panel">
          <div className="panel-head"><h3>Outcome mix</h3><span className="hint">of {mix.total} leads</span></div>
          <Mix label="Converted" count={mix.converted} total={mix.total} cls="converted" />
          <Mix label="Dropped" count={mix.dropped} total={mix.total} cls="dropped" />
          <Mix label="Follow-up required" count={mix.follow_up_required} total={mix.total} cls="upcoming" />
          <Mix label="Not analyzed yet" count={mix.not_analyzed} total={mix.total} cls="neutral" />
        </section>

        <section className="panel">
          <div className="panel-head">
            <h3>How leads sounded</h3>
            <span className="hint">of {tone.total} leads</span>
          </div>
          <Mix label="Positive" count={tone.positive} total={tone.total} cls="converted" />
          <Mix label="Mixed" count={tone.mixed} total={tone.total} cls="due" />
          <Mix label="Neutral" count={tone.neutral} total={tone.total} cls="neutral" />
          <Mix label="Negative" count={tone.negative} total={tone.total} cls="dropped" />
          <Mix label="Not assessed" count={tone.not_assessed} total={tone.total} cls="neutral" />
          <p className="sub" style={{ marginTop: 8 }}>
            <strong>{tone.declining}</strong> call{tone.declining === 1 ? '' : 's'} ended worse
            than they started — counted again above, since a declining call can still average
            out neutral. Those leads sort first in the worklist.
          </p>
        </section>
      </div>

      {(overview.unscheduled_total > 0 || overview.unassigned_total > 0 ||
        overview.unanalyzed_calls > 0 || overview.degraded_analyses > 0) && (
        <div className="callouts">
          {overview.unscheduled_total > 0 && (
            <div className="banner warn">
              <span>
                <strong>{overview.unscheduled_total}</strong>{' '}
                {overview.unscheduled_total === 1
                  ? 'lead needs a follow-up but has no date, so it appears'
                  : 'leads need a follow-up but have no date, so they appear'}{' '}
                on no timeline and no reminder lists them.
              </span>
            </div>
          )}
          {overview.unanalyzed_calls > 0 && (
            <div className="banner info">
              <span>
                <strong>{overview.unanalyzed_calls}</strong>{' '}
                {overview.unanalyzed_calls === 1 ? 'call has' : 'calls have'} a transcript or a
                recording but no analysis yet, so {overview.unanalyzed_calls === 1 ? 'its' : 'their'}{' '}
                outcome and follow-up are unknown. Open the lead and analyse the call.
              </span>
            </div>
          )}
          {overview.degraded_analyses > 0 && (
            <div className="banner warn">
              <span>
                <strong>{overview.degraded_analyses}</strong>{' '}
                {overview.degraded_analyses === 1 ? 'analysis was' : 'analyses were'} produced by
                the deterministic fallback rather than the model, and {overview.degraded_analyses === 1 ? 'it is' : 'they are'}{' '}
                already written to the lead. Treat those outcomes as provisional.
              </span>
            </div>
          )}
          {overview.unassigned_total > 0 && (
            <div className="banner error">
              <span>
                <strong>{overview.unassigned_total}</strong>{' '}
                {overview.unassigned_total === 1
                  ? 'pending follow-up has no assigned BD, so nobody can be reminded about it.'
                  : 'pending follow-ups have no assigned BD, so nobody can be reminded about them.'}
              </span>
            </div>
          )}
        </div>
      )}

      <section className="panel" style={{ padding: 0, marginTop: 16 }}>
        <div className="panel-head" style={{ padding: 16, marginBottom: 0 }}>
          <h3>Workload by BD</h3>
          <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            <span className="hint">Rescheduled is all-time · repeated pushes mean a lead being avoided</span>
            <button className="btn small" disabled={!rows.length}
              onClick={() => downloadCsv(csvName('workload-by-bd', rows.length), rows, BD_CSV)}>
              Export CSV
            </button>
          </div>
        </div>
        {!rows.length ? (
          <div className="empty"><div className="empty-title">No BDs to show</div></div>
        ) : (
          <div className="table-wrap" style={{ border: 'none', borderRadius: 0, maxHeight: 'none' }}>
            <table>
              <thead>
                <tr>
                  {SORTABLE.map(([key, label, num]) => (
                    <th key={key} className={num ? 'num' : ''}>
                      <button className="th-sort" onClick={() => toggleSort(key)}>
                        {label}{sort.key === key ? (sort.dir === 'asc' ? ' ↑' : ' ↓') : ''}
                      </button>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.bd_id}>
                    <td>
                      <div className="lead-name truncate" title={r.bd_email || r.bd_name}>{r.bd_name}</div>
                      <div className="sub">
                        {r.orphaned
                          ? (r.bd_id === '__unassigned__' ? 'no BD assigned' : 'caller record missing')
                          : r.active === false ? 'inactive' : r.bd_email || r.bd_id}
                      </div>
                    </td>
                    <td className="num">{r.assigned}</td>
                    <td className="num">{r.overdue ? <strong style={{ color: 'var(--overdue-fg)' }}>{r.overdue}</strong> : 0}</td>
                    <td className="num">{r.due_today}</td>
                    <td className="num">{r.upcoming}</td>
                    <td className="num">{r.unscheduled}</td>
                    <td className="num">{r.rescheduled_all_time}</td>
                    <td className="num">{r.converted}</td>
                    <td className="num">{r.dropped}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="panel" style={{ marginTop: 16 }}>
        <div className="panel-head"><h3>Reminder delivery</h3></div>
        {!history.length ? (
          <p className="sub" style={{ margin: 0 }}>No reminder has been sent yet.</p>
        ) : (
          <div className="kv">
            {history.slice(0, 5).map((h) => (
              <Frag key={h.alert_id}>
                <dt title={formatDateTime(h.sent_at)}>{relativeToNow(h.sent_at)}</dt>
                <dd>
                  <span className={`badge ${h.status === 'SENT' ? 'converted' : h.status === 'FAILED' ? 'overdue' : 'neutral'}`}>
                    {h.status.replaceAll('_', ' ').toLowerCase()}
                  </span>
                  {' '}{h.bd_name || h.bd_id} · {h.overdue_count} overdue, {h.due_today_count} due
                  {h.error && <div className="sub">{h.error}</div>}
                </dd>
              </Frag>
            ))}
          </div>
        )}
      </section>

      {/* Absence of trends is a data limitation, not an oversight. Say so. */}
      <p className="sub" style={{ marginTop: 16 }}>
        <strong>No trends shown.</strong> Nothing records the daily size of the queue — the
        follow-up history stores actions taken, not backlog over time — so week-over-week change
        cannot be computed honestly. Adding a nightly snapshot would make it possible.
        {overview.generated_at && <> Generated {formatDateTime(overview.generated_at)}.</>}
      </p>
    </>
  )
}

const Frag = ({ children }) => <>{children}</>

function Kpi({ label, value, sub, cls }) {
  return (
    <div className={`card ${cls}`}>
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      <div className="sub">{sub}</div>
    </div>
  )
}

function Mix({ label, count, total, cls }) {
  // With no denominator there is no honest percentage, so show a dash.
  const pct = total > 0 ? Math.round((count / total) * 100) : null
  return (
    <div className="mix-row">
      <span className={`badge ${cls}`}>{label}</span>
      <svg className="mix-bar" viewBox="0 0 100 8" preserveAspectRatio="none" aria-hidden="true">
        <rect x="0" y="0" width="100" height="8" rx="1" fill="var(--surface-3)" />
        <rect x="0" y="0" width={pct ?? 0} height="8" rx="1" fill={`var(--${cls === 'neutral' ? 'dropped' : cls}-dot)`} />
      </svg>
      <span className="mix-value">{count} <span className="sub">/ {total}</span></span>
      <span className="mix-pct">{pct === null ? '—' : `${pct}%`}</span>
    </div>
  )
}

function InsightsSkeleton() {
  return (
    <>
      <div className="cards">
        {[...Array(5)].map((_, i) => (
          <div className="card" key={i}>
            <div className="skeleton skeleton-line" style={{ width: '60%' }} />
            <div className="skeleton skeleton-line" style={{ width: 44, height: 22 }} />
          </div>
        ))}
      </div>
      <div className="insights-grid">
        {[0, 1].map((i) => (
          <div className="panel" key={i}>
            <div className="skeleton skeleton-line" style={{ width: 180, marginBottom: 16 }} />
            {[...Array(4)].map((_, j) => <div className="skeleton skeleton-line" key={j} style={{ height: 16 }} />)}
          </div>
        ))}
      </div>
    </>
  )
}
