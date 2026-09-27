/** Command palette (⌘K / Ctrl-K).
 *
 *  The chat list is the only way to reach past work, and it grows without bound. A palette makes
 *  every chat, tab and action reachable in two keystrokes, which matters most for the case this
 *  dashboard is actually used in: a long run is going and you want to look at something else
 *  without losing your place.
 *
 *  Accessibility: it is a modal dialog, so it traps focus, restores it on close, exposes a
 *  listbox/option tree to screen readers, and is fully operable from the keyboard alone.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { C, Dot } from './panels'

function score(query, text) {
  const q = query.toLowerCase().trim()
  const s = (text || '').toLowerCase()
  if (!q) return 1
  if (s.startsWith(q)) return 3
  if (s.includes(q)) return 2
  let i = 0                                   // subsequence match, so "rdm" finds "roadmap"
  for (const ch of s) if (ch === q[i]) i++
  return i === q.length ? 1 : 0
}

export function CommandPalette({ open, onClose, actions }) {
  const [q, setQ] = useState('')
  const [active, setActive] = useState(0)
  const inputRef = useRef(null)
  const listRef = useRef(null)
  const restoreRef = useRef(null)

  const items = useMemo(() => {
    const scored = actions
      .map((a) => ({ ...a, _s: Math.max(score(q, a.label), score(q, a.group) * 0.5) }))
      .filter((a) => a._s > 0)
    scored.sort((a, b) => b._s - a._s)
    return scored.slice(0, 40)
  }, [actions, q])

  useEffect(() => { setActive(0) }, [q])

  useEffect(() => {
    if (!open) return
    restoreRef.current = document.activeElement
    setQ('')
    const id = requestAnimationFrame(() => inputRef.current?.focus())
    return () => {
      cancelAnimationFrame(id)
      restoreRef.current?.focus?.()          // focus goes back where it came from
    }
  }, [open])

  useEffect(() => {                           // keep the active row in view
    listRef.current?.querySelector('[data-active="true"]')?.scrollIntoView({ block: 'nearest' })
  }, [active, items])

  if (!open) return null

  const run = (item) => { onClose(); item?.run?.() }

  const onKeyDown = (e) => {
    if (e.key === 'Escape') { e.preventDefault(); onClose() }
    else if (e.key === 'ArrowDown') { e.preventDefault(); setActive((i) => Math.min(items.length - 1, i + 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((i) => Math.max(0, i - 1)) }
    else if (e.key === 'Home') { e.preventDefault(); setActive(0) }
    else if (e.key === 'End') { e.preventDefault(); setActive(items.length - 1) }
    else if (e.key === 'Enter') { e.preventDefault(); run(items[active]) }
    else if (e.key === 'Tab') { e.preventDefault() }   // focus stays inside the dialog
  }

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center pt-[12vh] px-4 bg-black/60"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div role="dialog" aria-modal="true" aria-label="Command palette"
        className="w-full max-w-lg overflow-hidden fade-in bg-card border border-rule2 ticks shadow-[0_24px_60px_-20px_rgba(0,0,0,.6)]">
        <div className="flex items-center gap-3 px-4 py-3 border-b border-rule">
          <span aria-hidden="true" className="font-mono text-signal text-[14px]">›</span>
          <input ref={inputRef} value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={onKeyDown}
            role="combobox" aria-expanded="true" aria-controls="cmdk-list" aria-autocomplete="list"
            aria-activedescendant={items[active] ? `cmdk-${active}` : undefined}
            placeholder="Find a conversation, view or action…" aria-label="Search conversations, views and actions"
            className="flex-1 bg-transparent outline-none focus-visible:outline-none text-[15.5px] text-ink placeholder:text-faint" />
          <kbd className="num text-[9px] text-muted border border-rule2 px-1 py-px">esc</kbd>
        </div>

        <ul id="cmdk-list" ref={listRef} role="listbox" aria-label="Results"
          className="max-h-[52vh] overflow-auto py-1">
          {items.length === 0 && (
            <li className="px-4 py-6 text-center text-[14px] text-muted">
              Nothing matches “{q}”.
            </li>
          )}
          {items.map((it, i) => (
            <li key={it.id} id={`cmdk-${i}`} role="option" aria-selected={i === active} data-active={i === active}
              onMouseEnter={() => setActive(i)} onMouseDown={(e) => e.preventDefault()} onClick={() => run(it)}
              className={`flex items-center gap-2.5 px-4 py-2 cursor-pointer text-[14px] border-l-2 ${
                i === active ? 'bg-paper3/60 text-ink border-signal' : 'text-ink2 border-transparent'}`}>
              {it.dot && <Dot c={C[it.dot] || C.idle} />}
              <span className="truncate flex-1">{it.label}</span>
              <span className="label shrink-0">{it.group}</span>
            </li>
          ))}
        </ul>

        <div className="flex gap-4 px-4 py-1.5 border-t border-rule label">
          <span>↑↓ move</span><span>↵ open</span><span>esc close</span>
        </div>
      </div>
    </div>
  )
}
