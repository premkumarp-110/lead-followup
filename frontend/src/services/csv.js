/** CSV export from rows already in memory -- no extra round trip to the API. */

/**
 * Escape one cell.
 *
 * Beyond normal quoting, a value starting with = + - @ is prefixed with a
 * single quote. Spreadsheets treat those as formulas, so an exported lead
 * named "=cmd|..." becomes executable content in Excel. The prefix makes the
 * cell render as text.
 */
function cell(value) {
  if (value === null || value === undefined) return ''
  let text = String(value)
  if (/^[=+\-@\t\r]/.test(text)) text = `'${text}`
  return /[",\n\r]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text
}

/** `columns` is [{ key, label, get? }]. */
export function toCsv(rows, columns) {
  const head = columns.map((c) => cell(c.label)).join(',')
  const body = rows.map((row) =>
    columns.map((c) => cell(c.get ? c.get(row) : row[c.key])).join(','),
  )
  // BOM so Excel opens UTF-8 (Indian names, the rupee sign) without mangling it.
  return `﻿${[head, ...body].join('\r\n')}\r\n`
}

export function downloadCsv(filename, rows, columns) {
  const blob = new Blob([toCsv(rows, columns)], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

/** A filename that says what is inside it: scope, row count, date. */
export function csvName(base, count, scope) {
  const date = new Date().toISOString().slice(0, 10)
  const parts = [base, scope, `${count}-rows`, date].filter(Boolean)
  return `${parts.join('_').replace(/[^\w.-]+/g, '-')}.csv`
}
