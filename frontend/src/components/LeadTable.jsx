import { badgeFor, formatDate, formatDateTime, formatFollowUp, outcomeBadge } from './format.js'

/**
 * Renders both dashboard tables.
 * variant="follow-up" -> the active worklist, with follow-up columns + actions
 * variant="closed"    -> converted / dropped / completed leads
 */
export default function LeadTable({ variant, leads, loading, onSelectLead, onAction }) {
  const isFollowUp = variant === 'follow-up'

  if (loading) return <div className="table-wrap"><div className="empty">Loading…</div></div>

  if (!leads.length) {
    return (
      <div className="table-wrap">
        <div className="empty">
          {isFollowUp
            ? 'No leads need follow-up for these filters.'
            : 'No closed leads match these filters.'}
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
            <th>Course</th>
            <th>BD</th>
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
            return (
              <tr key={lead.lead_id} className="row-clickable" onClick={() => onSelectLead(lead.lead_id)}>
                <td>
                  <div className="lead-name">{lead.name}</div>
                  <div className="sub">{lead.lead_id} · {lead.phone}</div>
                </td>
                <td>{lead.course}</td>
                <td>{lead.assigned_bd?.name}</td>
                <td>{formatDate(lead.last_call_at)}</td>

                {isFollowUp ? (
                  <>
                    <td className={lead.follow_up?.datetime ? '' : 'sub'}>{formatFollowUp(lead.follow_up)}</td>
                    <td><span className={`badge ${badge.cls}`}>{badge.label}</span></td>
                    <td className="reason-cell" title={lead.follow_up?.reason || ''}>{lead.follow_up?.reason || '—'}</td>
                    <td onClick={(e) => e.stopPropagation()}>
                      <div className="actions">
                        <button className="btn small" onClick={() => onAction(lead, 'COMPLETE')}>Mark Done</button>
                        <button className="btn small" onClick={() => onAction(lead, 'RESCHEDULE')}>Reschedule</button>
                        <button className="btn small" onClick={() => onAction(lead, 'CANCEL')}>Cancel</button>
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
