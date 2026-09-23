import {
  badgeFor, formatDate, formatDateTime, formatFollowUp, formatProduct,
  outcomeBadge, relativeToNow, sentimentBadge, stageStep, trajectoryMark,
} from './format.js'

/**
 * Both dashboard tables.
 *   variant="follow-up" -> the active worklist, with follow-up columns + actions
 *   variant="closed"    -> converted / dropped / completed leads
 *
 * The header is sticky (see .table-wrap in index.css) so column meanings stay
 * visible while scrolling a long list.
 */
export default function LeadTable({ variant, leads, loading, hasFilters, onSelectLead, onAction, onClearFilters }) {
  const isFollowUp = variant === 'follow-up'

  if (loading) return <TableSkeleton isFollowUp={isFollowUp} />

  if (!leads.length) {
    // "Nothing here" and "nothing matches what you asked for" are different
    // problems, and only one of them has an action attached.
    return (
      <div className="table-wrap">
        <div className="empty">
          {hasFilters ? (
            <>
              <div className="empty-title">No leads match these filters</div>
              <div className="empty-hint">Try widening the date range or clearing a filter.</div>
              <div className="empty-actions">
                <button className="btn" onClick={onClearFilters}>Clear filters</button>
              </div>
            </>
          ) : (
            <>
              <div className="empty-title">
                {isFollowUp ? 'No lead needs follow-up' : 'No closed leads yet'}
              </div>
              <div className="empty-hint">
                {isFollowUp
                  ? 'Every follow-up is either completed, cancelled, or not required.'
                  : 'Leads appear here once they convert, drop, or their follow-up is closed.'}
              </div>
            </>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Lead</th>
            <th>Product</th>
            <th>Stage</th>
            <th>BD</th>
            <th className="num">Calls</th>
            <th>Sentiment</th>
            <th>Last Call</th>
            {isFollowUp ? (
              <>
                <th>Follow-up</th>
                <th>Status</th>
                <th>Reason</th>
                <th>Action</th>
              </>
            ) : (
              <>
                <th>Outcome</th>
                <th>Last Updated</th>
                <th>Action</th>
              </>
            )}
          </tr>
        </thead>
        <tbody>
          {leads.map((lead) => {
            const badge = badgeFor(lead)
            const outcome = outcomeBadge(lead.latest_outcome, lead.lead_status)
            const overdue = lead.follow_up?.bucket === 'OVERDUE'
            return (
              <tr
                key={lead.lead_id}
                className={`row-clickable ${overdue ? 'is-urgent' : ''}`}
                onClick={() => onSelectLead(lead.lead_id)}
              >
                <td>
                  {/* No name/phone/email exists upstream -- the id is the identity,
                      and external_id is the link back into LeadSquared. */}
                  <div className="lead-name mono truncate" title={lead.lead_id}>
                    {lead.lead_id}
                  </div>
                  <div className="sub truncate" title={lead.external_id || ''}>
                    {lead.external_id || 'no external id'}
                  </div>
                </td>
                <td>{formatProduct(lead.product)}</td>
                <td>
                  {lead.stage ? (
                    <span className={`chip ${stageStep(lead.stage) >= 3 ? 'chip-warn' : ''}`}>
                      {lead.stage}
                    </span>
                  ) : '—'}
                </td>
                <td title={lead.owner_email || ''}>
                  {/* .truncate sets display:block, which removes a <td> from the
                      table formatting context -- it must live on an inner div. */}
                  <div className="truncate">
                    {lead.owner_name || <span className="sub">Unassigned</span>}
                  </div>
                </td>
                <td className="num" title="Connected / total attempts">
                  {lead.total_attempts ? `${lead.calls_connected}/${lead.total_attempts}` : '—'}
                </td>
                <td>
                  {lead.latest_sentiment ? (() => {
                    const tone = sentimentBadge(lead.latest_sentiment)
                    const traj = trajectoryMark(lead.latest_sentiment)
                    return (
                      <span className={`badge ${tone.cls}`}>
                        {tone.label}
                        {traj && <span className={traj.cls} title={traj.title}>{traj.mark}</span>}
                      </span>
                    )
                  })() : <span className="sub">—</span>}
                </td>
                <td>{formatDate(lead.last_call_at)}</td>

                {isFollowUp ? (
                  <>
                    <td className={lead.follow_up?.datetime ? '' : 'sub'}>
                      {formatFollowUp(lead.follow_up)}
                      {lead.follow_up?.datetime && (
                        <div className="sub">{relativeToNow(lead.follow_up.datetime)}</div>
                      )}
                    </td>
                    <td><span className={`badge ${badge.cls}`}>{badge.label}</span></td>
                    <td className="reason-cell" title={lead.follow_up?.reason || ''}>
                      <div className="clamp-2">{lead.follow_up?.reason || '—'}</div>
                    </td>
                    <td onClick={(e) => e.stopPropagation()}>
                      <div className="actions">
                        <button className="btn small primary" onClick={() => onAction(lead, 'RESCHEDULE')}>Reschedule</button>
                      </div>
                    </td>
                  </>
                ) : (
                  <>
                    <td><span className={`badge ${outcome.cls}`}>{outcome.label}</span></td>
                    <td>{formatDateTime(lead.updated_at)}</td>
                    <td onClick={(e) => e.stopPropagation()}>
                      <button className="btn small" onClick={() => onSelectLead(lead.lead_id)}>View</button>
                    </td>
                  </>
                )}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function TableSkeleton({ isFollowUp }) {
  const widths = isFollowUp
    ? [150, 120, 90, 90, 50, 90, 80, 120, 80, 200, 190]
    : [150, 120, 90, 90, 50, 90, 80, 120, 120, 70]
  return (
    <div className="table-wrap">
      {[...Array(6)].map((_, r) => (
        <div className="skeleton-row" key={r}>
          {widths.map((w, i) => <span className="skeleton" key={i} style={{ width: w }} />)}
        </div>
      ))}
    </div>
  )
}
