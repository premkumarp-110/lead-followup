/**
 * localStorage that cannot throw.
 *
 * Private-mode Safari and hardened browser settings make `localStorage`
 * throw on access, not just return null. Every read and write is guarded and
 * falls back to an in-memory map, so a stored preference degrades to
 * "forgotten on reload" instead of breaking the page.
 */

const memory = new Map()

export function readStored(key, fallback = null) {
  try {
    const value = window.localStorage.getItem(key)
    return value === null ? fallback : value
  } catch {
    return memory.has(key) ? memory.get(key) : fallback
  }
}

export function writeStored(key, value) {
  memory.set(key, value)
  try {
    if (value === null || value === undefined) window.localStorage.removeItem(key)
    else window.localStorage.setItem(key, value)
  } catch {
    /* in-memory fallback above is enough */
  }
}
