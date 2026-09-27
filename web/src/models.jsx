/** The model picker in the composer: a button showing the model in use, opening a searchable list of
 *  every model Synapse can reach right now — pulled Ollama models and the hosted provider's models.
 *  The choice is saved on the server; a run keeps the model it started with. */
import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from './api'

const GROUP = (m) => (m.local ? 'On this machine · Ollama' : `Hosted · ${m.provider}`)

export function ModelPicker({ health, onChanged }) {
  const [open, setOpen] = useState(false)
  const [list, setList] = useState(null)
  const [q, setQ] = useState('')
  const [cursor, setCursor] = useState(0)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [pendingEnter, setPendingEnter] = useState(false)   // Enter pressed before the list arrived
  const wrapRef = useRef(null)
  const searchRef = useRef(null)

  const current = health?.model_id

  // Open/close reset their state in the same event as the click, never later in an effect — keys typed
  // straight after clicking must land in a fresh, empty search box.
  const openPicker = () => {
    setErr(''); setQ(''); setCursor(0); setPendingEnter(false); setOpen(true)
    // the last list shows at once (so typing + Enter works immediately); a fresh one replaces it
    api.models().then((d) => setList(d.models)).catch(() => setErr('Could not load models'))
  }
  const closePicker = () => { setOpen(false); setPendingEnter(false); setQ('') }

  useEffect(() => {
    if (!open) return
    const close = (e) => { if (!wrapRef.current?.contains(e.target)) closePicker() }
    window.addEventListener('mousedown', close)
    return () => window.removeEventListener('mousedown', close)
  }, [open])

  const shown = useMemo(() => {
    const words = q.toLowerCase().split(/\s+/).filter(Boolean)
    return (list || []).filter((m) => words.every((w) => `${m.model} ${m.provider} ${m.size || ''}`.toLowerCase().includes(w)))
  }, [list, q])


  useEffect(() => {
    if (pendingEnter && list) { setPendingEnter(false); if (shown[0]) pick(shown[0]) }
  }, [pendingEnter, list, shown])  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    wrapRef.current?.querySelector(`[data-i="${cursor}"]`)?.scrollIntoView({ block: 'nearest' })
  }, [cursor])

  const pick = async (m) => {
    if (!m || m.offline) return
    setBusy(true); setErr('')
    try {
      await api.setModel(m.id)
      closePicker()
      onChanged?.()
    } catch (e) {
      setErr(String(e.message).replace(/[{}"[\]]/g, '').slice(0, 120))
    } finally {
      setBusy(false)
    }
  }

  const onKey = (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setCursor((c) => Math.min(c + 1, shown.length - 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setCursor((c) => Math.max(c - 1, 0)) }
    else if (e.key === 'Enter') { e.preventDefault(); if (list) pick(shown[cursor]); else setPendingEnter(true) }
    else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closePicker(); wrapRef.current?.querySelector('button')?.focus() }
  }

  const label = health?.ok ? health.model : '—'
  let lastGroup = null

  return (
    <div ref={wrapRef} className="relative shrink-0">
      <button type="button" onClick={() => (open ? closePicker() : openPicker())} disabled={!health?.ok}
        aria-haspopup="listbox" aria-expanded={open} aria-label={`Model: ${label}. Change model`}
        title="Choose the model"
        className="btn-line !px-2 !py-[5px] !normal-case !tracking-normal max-w-[210px] disabled:opacity-50">
        <span aria-hidden="true" className={`w-[6px] h-[6px] shrink-0 ${health?.local ? 'bg-ink2' : 'bg-signal'}`} />
        <span className="truncate text-[11px]">{label}</span>
        <span aria-hidden="true" className="text-faint text-[9px]">▾</span>
      </button>

      {open && (
        <div role="dialog" aria-label="Choose a model"
          className="absolute bottom-[calc(100%+10px)] right-0 z-50 w-[340px] max-w-[calc(100vw-32px)] bg-card border border-rule2 shadow-2xl animate-rise">
          <div className="flex items-center gap-2 px-3 border-b border-rule">
            <span aria-hidden="true" className="font-mono text-[12px] text-signal">⌕</span>
            <input ref={searchRef} autoFocus value={q} onChange={(e) => { setQ(e.target.value); setCursor(0) }} onKeyDown={onKey}
              placeholder="Search models…" aria-label="Search models" role="combobox" aria-expanded="true"
              aria-controls="model-list" aria-activedescendant={shown[cursor] ? `model-${cursor}` : undefined}
              className="flex-1 bg-transparent outline-none focus-visible:outline-none py-2.5 text-[13.5px] text-ink placeholder:text-faint" />
            {busy && <span className="label !text-signalink">saving…</span>}
          </div>
          <ul id="model-list" role="listbox" aria-label="Models" className="max-h-[300px] overflow-auto py-1">
            {!list && !err && <li className="px-3 py-2 label">Loading…</li>}
            {list && shown.length === 0 && <li className="px-3 py-2 text-[13px] text-muted">No model matches “{q}”.</li>}
            {shown.map((m, i) => {
              const g = GROUP(m)
              const head = g !== lastGroup
              lastGroup = g
              const sel = m.id === current
              return (
                <li key={m.id} role="presentation">
                  {head && <div className="label px-3 pt-2.5 pb-1">{g}</div>}
                  <button type="button" role="option" id={`model-${i}`} data-i={i} aria-selected={sel}
                    disabled={m.offline} onMouseEnter={() => setCursor(i)} onClick={() => pick(m)}
                    className={`w-full text-left px-3 py-1.5 flex items-baseline gap-2 ${
                      i === cursor ? 'bg-paper3' : ''} ${m.offline ? 'opacity-45 cursor-not-allowed' : ''}`}>
                    <span className={`w-3 shrink-0 font-mono text-[10px] ${sel ? 'text-signalink' : 'text-transparent'}`} aria-hidden="true">✓</span>
                    <span className={`min-w-0 truncate font-mono text-[11.5px] ${sel ? 'text-ink' : 'text-ink2'}`}>{m.model}</span>
                    <span className="ml-auto shrink-0 num text-[9px] text-faint">{m.size || ''}</span>
                  </button>
                </li>
              )
            })}
          </ul>
          <div className="px-3 py-2 border-t border-rule text-[11.5px] leading-snug text-muted">
            {err ? <span className="text-alert">{err}</span>
              : health?.fallback ? 'Hosted models fall back to your local model if they fail.'
                : 'Applies from your next request.'}
          </div>
        </div>
      )}
    </div>
  )
}
