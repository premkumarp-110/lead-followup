import { useEffect, useId, useMemo, useRef, useState } from 'react'

/**
 * Searchable BD (owner) picker.
 *
 * A native <select> was fine for five seeded BDs. Against the real CRM there
 * are 100+ distinct owners, which is unusable as a scroll list -- hence the
 * search. It is hand-rolled rather than pulling in react-select or SlimSelect:
 * the project has no UI dependencies at all, and this is one control.
 *
 * Names collide (duplicate first names are common in the real data), so the
 * email is shown as a secondary line and is searchable. The VALUE is still the
 * caller_id -- the backend's `bd` filter accepts either that or the email.
 *
 * It scopes the worklist and reminders. It is a convenience, NOT an access
 * control: every BD's data stays readable through the API regardless.
 */
export default function BDSelector({ callers, value, onChange, allLabel = 'All BDs', id }) {
  const generatedId = useId()
  const baseId = id || generatedId
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)

  const wrapRef = useRef(null)
  const inputRef = useRef(null)
  const listRef = useRef(null)

  const list = useMemo(() => callers || [], [callers])

  // The email is shown to tell apart BDs with similar names. When every BD
  // shares one address -- as the seeded data deliberately does, so reminder
  // digests cannot reach a real colleague -- it distinguishes nothing and is
  // just twelve identical lines of noise, so drop it.
  const emailDisambiguates = useMemo(() => {
    const emails = list.map((c) => c.email).filter(Boolean)
    return new Set(emails).size > 1
  }, [list])

  // A stored BD that no longer exists must read as "All BDs", not as a blank
  // control that silently filters everything to nothing.
  const selected = list.find((c) => c.caller_id === value) || null

  const options = useMemo(() => {
    const q = query.trim().toLowerCase()
    const matches = q
      ? list.filter(
          (c) =>
            (c.name || '').toLowerCase().includes(q) ||
            (c.email || '').toLowerCase().includes(q),
        )
      : list
    return [{ caller_id: '', name: allLabel, email: null, all: true }, ...matches]
  }, [list, query, allLabel])

  // Keep the highlight inside the list as it shrinks while typing.
  useEffect(() => { setActive(0) }, [query])

  useEffect(() => {
    if (!open) return
    function onDocDown(e) {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) close()
    }
    document.addEventListener('mousedown', onDocDown)
    return () => document.removeEventListener('mousedown', onDocDown)
  }, [open]) // eslint-disable-line react-hooks/exhaustive-deps

  // Keep the active option scrolled into view for keyboard-only use.
  useEffect(() => {
    if (!open || !listRef.current) return
    const el = listRef.current.querySelector(`[data-index="${active}"]`)
    if (el) el.scrollIntoView({ block: 'nearest' })
  }, [active, open])

  function close() {
    setOpen(false)
    setQuery('')
  }

  function choose(option) {
    onChange(option.caller_id || null)
    close()
    inputRef.current?.blur()
  }

  function onKeyDown(e) {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      if (!open) { setOpen(true); return }
      const step = e.key === 'ArrowDown' ? 1 : -1
      // Roll over rather than sticking at the ends.
      setActive((i) => (i + step + options.length) % options.length)
      return
    }
    if (e.key === 'Enter') {
      if (!open) { setOpen(true); return }
      e.preventDefault()
      if (options[active]) choose(options[active])
      return
    }
    if (e.key === 'Escape') {
      if (open) { e.stopPropagation(); close() }
      return
    }
    if (e.key === 'Tab') close()
  }

  // While closed the input shows the selection; while open it shows what is
  // being typed. Blurring mid-query reverts rather than stranding the text.
  const display = open ? query : selected ? selected.name : ''

  return (
    <div className="field combo" ref={wrapRef} style={{ minWidth: 0 }}>
      <input
        ref={inputRef}
        id={baseId}
        type="text"
        className="combo-input"
        role="combobox"
        aria-expanded={open}
        aria-controls={`${baseId}-list`}
        aria-autocomplete="list"
        aria-activedescendant={open && options[active] ? `${baseId}-opt-${active}` : undefined}
        aria-label="Acting as"
        title="Scopes the worklist and reminders. Not a login."
        autoComplete="off"
        placeholder={allLabel}
        value={display}
        onChange={(e) => { setQuery(e.target.value); setOpen(true) }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKeyDown}
      />
      {selected && !open && (
        <button
          type="button"
          className="combo-clear"
          aria-label={`Clear ${selected.name}`}
          onClick={() => onChange(null)}
        >
          &times;
        </button>
      )}

      {open && (
        <ul className="combo-list" id={`${baseId}-list`} role="listbox" ref={listRef}>
          {options.length === 1 && query.trim() ? (
            <li className="combo-empty">No BD matches “{query.trim()}”</li>
          ) : (
            options.map((option, index) => (
              <li
                key={option.caller_id || '__all__'}
                id={`${baseId}-opt-${index}`}
                data-index={index}
                role="option"
                aria-selected={option.caller_id === (value || '')}
                className={`combo-option ${index === active ? 'active' : ''} ${option.all ? 'is-all' : ''}`}
                // mousedown, not click: the input's blur would close the list first.
                onMouseDown={(e) => { e.preventDefault(); choose(option) }}
                onMouseEnter={() => setActive(index)}
              >
                <span className="combo-option-name">
                  {option.name}
                  {option.active === false && <span className="combo-tag">inactive</span>}
                </span>
                {option.email && emailDisambiguates && (
                  <span className="combo-option-sub">{option.email}</span>
                )}
                {typeof option.lead_count === 'number' && option.lead_count > 0 && (
                  <span className="combo-count">{option.lead_count}</span>
                )}
              </li>
            ))
          )}
        </ul>
      )}
    </div>
  )
}
