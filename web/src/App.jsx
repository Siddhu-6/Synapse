/** Synapse — the shell.
 *
 *  Left: the index (every conversation, by day). Centre: the journal — each exchange a node on one
 *  ink line. Right, on wide screens: the margin (pipeline + trace for the entry you have open).
 *
 *  Scrolling is deliberate, because the old build jumped to the bottom whenever you clicked a turn:
 *  - sending a message follows the new entry down, until you scroll up yourself;
 *  - opening a conversation (or an entry from the index) scrolls to the START of that entry;
 *  - "Show working" never scrolls: the entry's position on screen is held while it expands.        */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { api, setToken } from './api'
import { Markdown } from './md'
import { Agents, Glyph, Led, Mark, VaultGraph, fmt } from './panels'
import { Wordmark } from './brand'
import { CommandPalette } from './cmdk'
import { Margin } from './rail'
import { EmptyJournal, Entry } from './journal'
import { applyTheme, getTheme, onThemeChange } from './theme'
import { ModelPicker } from './models'

/** Shown when the API has SYNAPSE_API_TOKEN set and this browser has no (or a wrong) token. */
function Unlock() {
  const [t, setT] = useState('')
  return (
    <main className="h-full grid place-items-center px-5">
      <form className="w-full max-w-sm" onSubmit={(e) => { e.preventDefault(); if (t.trim()) { setToken(t.trim()); location.reload() } }}>
        <h1><Wordmark size={34} /></h1>
        <p className="mt-6 text-[15px] text-muted">This Synapse is protected. Enter the access token
          (<code className="font-mono text-[11.5px] text-ink">SYNAPSE_API_TOKEN</code>) — it is kept in this browser only.</p>
        <label htmlFor="token" className="label block mt-6 mb-1.5">Access token</label>
        <input id="token" type="password" autoComplete="current-password" autoFocus value={t} onChange={(e) => setT(e.target.value)}
          className="w-full bg-card border border-rule2 focus:border-signal outline-none focus-visible:outline-none px-3 py-2 font-mono text-[13px] text-ink" />
        <button type="submit" className="btn-ink mt-4" disabled={!t.trim()}>Unlock</button>
      </form>
    </main>
  )
}

/** A radio group: one tab stop, arrow keys move between options. */
function Segmented({ label, options, value, onPick }) {
  const ids = options.map((o) => o.id)
  return (
    <div role="radiogroup" aria-label={label} className="flex border border-rule2 w-fit"
      onKeyDown={(e) => {
        const d = e.key === 'ArrowRight' || e.key === 'ArrowDown' ? 1 : e.key === 'ArrowLeft' || e.key === 'ArrowUp' ? -1 : 0
        if (!d) return
        e.preventDefault()
        const next = ids[(ids.indexOf(value) + d + ids.length) % ids.length]
        onPick(next)
        e.currentTarget.querySelector(`[data-id="${next}"]`)?.focus()
      }}>
      {options.map((o) => (
        <button key={o.id} type="button" role="radio" aria-checked={value === o.id} data-id={o.id} title={o.hint}
          tabIndex={value === o.id ? 0 : -1} onClick={() => onPick(o.id)}
          className={`inline-flex items-center gap-1.5 px-2.5 py-1 font-mono font-medium text-[9.5px] uppercase tracking-[0.1em] transition-colors ${
            value === o.id ? 'bg-ink text-paper' : 'text-muted hover:text-ink'}`}>
                    {o.name}
        </button>
      ))}
    </div>
  )
}

/** Light / Dark. Follows the system until you choose. */
function Appearance() {
  const [mode, setMode] = useState(getTheme)
  useEffect(() => onThemeChange(() => setMode(getTheme())), [])
  return (
    <Segmented label="Theme" value={mode} onPick={(m) => { applyTheme(m); setMode(m) }}
      options={[{ id: 'dark', name: 'Dark' }, { id: 'light', name: 'Light' }]} />
  )
}

const TABS = ['Journal', 'Agents', 'Vault', 'Memory', 'Tools', 'Evals']
const newId = () => crypto.randomUUID().slice(0, 12)

function dayLabel(ts) {
  if (!ts) return 'Earlier'
  const d = new Date(ts * 1000)
  const today = new Date()
  const y = new Date(); y.setDate(today.getDate() - 1)
  if (d.toDateString() === today.toDateString()) return 'Today'
  if (d.toDateString() === y.toDateString()) return 'Yesterday'
  return d.toLocaleDateString([], { day: 'numeric', month: 'long', year: d.getFullYear() === today.getFullYear() ? undefined : 'numeric' })
}

/* ── secondary views ──────────────────────────────────────────────────────── */

function Heading({ kicker, title, children }) {
  return (
    <header className="mb-8">
      <div className="label">{kicker}</div>
      <h1 className="display text-[34px] leading-[1.05] mt-1.5">{title}</h1>
      {children && <p className="text-[14.5px] leading-relaxed text-muted mt-2 max-w-[64ch]">{children}</p>}
    </header>
  )
}

function Memory() {
  const [d, setD] = useState(null)
  const load = () => api.memory().then(setD).catch(() => setD({ facts: [], episodes: [] }))
  useEffect(() => { load() }, [])
  if (!d) return <p className="label">Loading…</p>
  return (
    <div className="max-w-[1000px] mx-auto">
      <Heading kicker="04 · Memory" title="What Synapse keeps">
        Facts are kept only when they are about you and useful later. Past runs are kept so follow-ups have context.
      </Heading>
      <div className="grid lg:grid-cols-2 gap-10">
        <section aria-labelledby="facts-h">
          <h2 id="facts-h" className="label !text-ink border-b border-rule2 pb-2">Semantic · {d.facts.length} facts</h2>
          {d.facts.length === 0 && <p className="py-3 text-muted">Nothing stored yet.</p>}
          <ul>
            {d.facts.map((f) => (
              <li key={f.id} className="group grid grid-cols-[5.5rem_1fr_auto] gap-x-3 items-baseline py-2 hair">
                <span className="label truncate">{f.kind}</span>
                <span className="text-[14.5px] text-ink2">{f.text}</span>
                <button type="button" onClick={() => api.forget(f.id).then(load)}
                  className="btn-text sm:opacity-0 group-hover:opacity-100 focus:opacity-100 hover:!text-alert"
                  aria-label={`Forget: ${f.text}`}>Forget</button>
              </li>
            ))}
          </ul>
        </section>
        <section aria-labelledby="eps-h">
          <h2 id="eps-h" className="label !text-ink border-b border-rule2 pb-2">Episodic · {d.episodes.length} runs</h2>
          {d.episodes.length === 0 && <p className="py-3 text-muted">No runs yet.</p>}
          <ul>
            {d.episodes.map((e) => {
              let arts = []
              try { arts = JSON.parse(e.artifacts || '[]') || [] } catch { /* older rows */ }
              return (
                <li key={e.run_id} className="py-2 hair">
                  <div className="flex items-baseline gap-3">
                    <Mark state={e.status} />
                    <span className="text-[14.5px] text-ink2 min-w-0">{e.goal}</span>
                  </div>
                  {arts.length > 0 && <div className="font-mono text-[10px] text-muted mt-1 break-all">{arts.join(' · ')}</div>}
                </li>
              )
            })}
          </ul>
        </section>
      </div>
    </div>
  )
}

const RISK = {
  high: ['Signature required', 'Always waits for your approval.'],
  medium: ['Signed when unsure', 'Needs approval when its input came from somewhere untrusted.'],
  low: ['Runs on its own', 'Read-only or easily undone.'],
}

function Tools() {
  const [t, setT] = useState(null)
  useEffect(() => { api.tools().then((d) => setT(d.tools)).catch(() => setT([])) }, [])
  if (!t) return <p className="label">Loading…</p>
  return (
    <div className="max-w-[1000px] mx-auto">
      <Heading kicker="05 · Tools" title={`${t.length} capabilities, behind one boundary`}>
        Every tool is an MCP call with a typed schema, a timeout and a risk class. The risk class decides whether
        Synapse asks you first.
      </Heading>
      {['high', 'medium', 'low'].map((risk) => {
        const items = t.filter((x) => x.risk === risk)
        if (!items.length) return null
        return (
          <section key={risk} className="mb-10" aria-labelledby={`risk-${risk}`}>
            <div className="flex flex-wrap items-baseline gap-x-4 border-b border-rule2 pb-2">
              <span className={`label ${risk === 'high' ? '!text-signalink' : '!text-ink'}`}>{risk} risk</span>
              <h2 id={`risk-${risk}`} className="display text-[20px] leading-none">{RISK[risk][0]}</h2>
              <span className="text-[13px] text-muted">{RISK[risk][1]}</span>
              <span className="ml-auto num text-[10px] text-muted">{items.length}</span>
            </div>
            <ul className="grid md:grid-cols-2 gap-x-10">
              {items.map((x) => (
                <li key={x.name} className="py-2.5 hair min-w-0">
                  <code className="font-mono text-[11.5px] text-ink">{x.name}</code>
                  <span className="ml-1 font-mono text-[10px] text-faint break-all">
                    ({Object.keys(x.params || {}).join(', ')})
                  </span>
                  <p className="text-[13.5px] text-muted leading-snug mt-0.5">{x.description}</p>
                </li>
              ))}
            </ul>
          </section>
        )
      })}
    </div>
  )
}

function Evals() {
  const [d, setD] = useState(null)
  const [err, setErr] = useState(false)
  useEffect(() => { api.evals().then(setD).catch(() => setErr(true)) }, [])
  if (err) {
    return (
      <div className="max-w-[1000px] mx-auto">
        <Heading kicker="06 · Evals" title="No report yet">
          Run <code className="font-mono text-[12px] text-ink">uv run python -m evals.run</code> and reload this page.
        </Heading>
      </div>
    )
  }
  if (!d) return <p className="label">Loading…</p>
  const all = d.passed === d.total
  return (
    <div className="max-w-[1000px] mx-auto">
      <Heading kicker={`06 · Evals · ${d.mode} · ${fmt(d.duration_ms)}`} title="Behaviour, checked end to end">
        Each scenario runs the real graph and asserts on what happened — tools chosen, arguments, approvals,
        memory, recovery — not on mocked return values.
      </Heading>
      <div className="flex items-baseline gap-4 mb-8">
        <span className={`display num text-[64px] leading-none ${all ? '' : '!text-alert'}`} style={{ fontStretch: '100%' }}>
          {d.passed}<span className="text-faint">/</span>{d.total}
        </span>
        <span className="label">scenarios passed</span>

      </div>
      <div className="grid lg:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)] gap-10">
        <table className="w-full text-[14px] border-t border-b border-rule2 self-start">
          <thead><tr>
            <th className="text-left label px-2 pt-2 pb-1.5 border-b border-rule2">Category</th>
            <th className="text-right label px-2 pt-2 pb-1.5 border-b border-rule2">Passed</th>
          </tr></thead>
          <tbody>
            {Object.entries(d.by_category).map(([c, v]) => (
              <tr key={c} className="border-b border-rule last:border-0">
                <td className="px-2 py-1.5 text-ink2">{c.replace(/_/g, ' ')}</td>
                <td className={`px-2 py-1.5 text-right num text-[11px] ${v.passed === v.total ? 'text-ink2' : 'text-alert font-medium'}`}>{v.passed}/{v.total}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <ol>
          {d.results.map((r) => (
            <li key={r.id} className="py-1.5 hair">
              <div className="flex items-baseline gap-3">
                <Mark state={r.passed ? 'ok' : 'failed'}>{r.passed ? 'pass' : 'fail'}</Mark>
                <code className="font-mono text-[11px] text-ink2 min-w-0 break-all">{r.id}</code>
                <span className="ml-auto num text-[9.5px] text-muted">{fmt(r.duration_ms)}</span>
              </div>
              {r.checks.filter((c) => !c.passed).map((c, i) => (
                <p key={i} className="text-[13px] text-ink ml-14 border-l-2 border-alert pl-2">{c.name}: {c.detail}</p>
              ))}
            </li>
          ))}
        </ol>
      </div>
    </div>
  )
}

function Vault({ data, note, onPick, onClose, onDeleted }) {
  const [confirm, setConfirm] = useState(false)
  const [err, setErr] = useState('')
  useEffect(() => { setConfirm(false); setErr('') }, [note?.path])
  const del = async () => {
    try { await api.deleteNote(note.path); onDeleted?.(note.path) } catch { setErr('Could not delete this note.') }
  }
  return (
    <div className="h-full min-h-[480px] grid lg:grid-cols-[minmax(0,1fr)_400px] gap-6">
      <figure className="panel ticks grid-paper relative min-h-[420px]">
        <figcaption className="absolute top-0 inset-x-0 z-10 flex items-center gap-3 px-4 py-2 border-b border-rule bg-card">
          <span className="label !text-ink">03 · Vault</span><span className="label !text-faint">notes and the wikilinks between them</span>
        </figcaption>
        <div className="absolute inset-0 top-9"><VaultGraph data={data} onPick={onPick} /></div>
      </figure>
      <section aria-label="Note" className="min-h-0 overflow-auto lg:border-l lg:border-rule lg:pl-6">
        {note ? (
          <>
            <div className="flex items-baseline gap-3 border-b border-rule2 pb-2 mb-3">
              <code className="font-mono text-[10.5px] text-ink break-all">{note.path}</code>
              <span className="ml-auto flex items-baseline gap-3 shrink-0">
                {!note.missing && (confirm ? (
                  <>
                    <span className="label !text-alert">Move to .trash?</span>
                    <button type="button" onClick={del} className="btn-text !text-alert">Delete</button>
                    <button type="button" onClick={() => setConfirm(false)} className="btn-text">Keep</button>
                  </>
                ) : (
                  <button type="button" onClick={() => setConfirm(true)} className="btn-text hover:!text-alert">Delete</button>
                ))}
                <button type="button" onClick={onClose} className="btn-text">Close</button>
              </span>
            </div>
            {err && <p role="alert" className="text-[13px] text-alert mb-2">{err}</p>}
            <Markdown text={note.content.slice(0, 12000)} />
          </>
        ) : (
          <p className="text-muted pt-2">Pick a note on the graph to read it here.
            Notes Synapse wrote are amber.</p>
        )}
      </section>
    </div>
  )
}

/* ── app ──────────────────────────────────────────────────────────────────── */

export default function App() {
  const [health, setHealth] = useState(null)
  const [runs, setRuns] = useState([])
  const [convos, setConvos] = useState([])
  const [convId, setConvId] = useState(newId)
  const [openId, setOpenId] = useState(null)         // the entry whose working is expanded
  const [detail, setDetail] = useState(null)         // full run for the entry we follow
  const [spans, setSpans] = useState([])
  const [live, setLive] = useState(null)
  const [draft, setDraft] = useState(null)          // { runId, text, final } — text being written right now
  const [sending, setSending] = useState(false)
  const [finishedId, setFinishedId] = useState(null) // the answer to reveal progressively
  const [goal, setGoal] = useState('')
  const [tab, setTab] = useState('Journal')
  const [vault, setVault] = useState(null)
  const [note, setNote] = useState(null)
  const [error, setError] = useState('')
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [navOpen, setNavOpen] = useState(false)
  const [menuId, setMenuId] = useState(null)            // conversation whose ⋮ menu is open
  const [locked, setLocked] = useState(false)          // API token required and missing/wrong

  const followRef = useRef(null)     // run id whose stream events we apply
  const scrollRef = useRef(null)
  const stickRef = useRef(false)     // follow the newest entry down (only after sending)
  const intentRef = useRef(null)     // { id } → scroll that entry's start into view once rendered
  const anchorRef = useRef(null)     // { id, top } → hold that entry still while it expands
  const inputRef = useRef(null)

  /* data ------------------------------------------------------------------ */
  const loadDetail = useCallback(async (id) => {
    const d = await api.run(id).catch(() => null)
    if (d && followRef.current === id) { setDetail(d); setSpans(d.spans || []) }
    return d
  }, [])

  const refreshSide = useCallback(() => {
    api.runs().then((d) => setRuns(d.runs)).catch(() => {})
    api.conversations().then((d) => setConvos(d.conversations)).catch(() => {})
  }, [])

  const follow = useCallback((id) => {
    followRef.current = id
    setLive(null)
    setDraft(null)
    if (!id) { setDetail(null); setSpans([]); return }
    loadDetail(id)
  }, [loadDetail])

  const openConversation = useCallback((cid, runList, entryId) => {
    const list = runList.filter((r) => r.conversation_id === cid)       // newest first
    const target = entryId || list[0]?.id
    setConvId(cid); setTab('Journal'); setNavOpen(false)
    stickRef.current = false
    setOpenId(target || null)
    follow(target || null)
    if (target) intentRef.current = { id: target }
  }, [follow])

  useEffect(() => {
    api.health().then(setHealth).catch((e) => { if (e.status === 401) setLocked(true); else setHealth({ ok: false }) })
    api.conversations().then((d) => setConvos(d.conversations)).catch(() => {})
    api.runs().then((d) => {
      setRuns(d.runs)
      if (d.runs[0]) openConversation(d.runs[0].conversation_id, d.runs)   // resume where you left off
    }).catch((e) => { if (e.status === 401) setLocked(true) })

    const es = api.stream('*', (msg) => {
      const mine = msg.run_id && msg.run_id === followRef.current
      if (msg.type === 'progress' && mine) {
        if (msg.partial != null) setDraft({ runId: msg.run_id, text: msg.partial, final: !!msg.final, actor: msg.actor })
        else if (msg.stage === 'step') setDraft(null)       // a new step started; its own text will stream in
        const { partial, ...rest } = msg
        setLive(rest)
      }
      if (msg.type === 'span' && mine) setSpans((s) => [...s, msg.span])
      if (msg.type === 'answer' && mine) {
        setDraft(null)
        setDetail((d) => (d && d.id === msg.run_id ? { ...d, answer: msg.answer } : d))
      }
      if (msg.type === 'approval' || msg.type === 'done') {
        if (mine) {
          setLive(null)
          if (msg.type === 'done') { setFinishedId(msg.run_id); setDraft(null) }
          loadDetail(msg.run_id)
        }
        refreshSide()
        if (msg.type === 'done') {
          api.health().then(setHealth).catch(() => {})
          setVault((v) => { if (v) api.vaultGraph().then(setVault).catch(() => {}); return v })
        }
      }
      if (msg.type === 'run_created') refreshSide()
    }, () => {
      // (re)connected — e.g. this tab was hidden and let go of its stream. Catch up on what was missed.
      refreshSide()
      api.health().then(setHealth).catch(() => {})
      if (followRef.current) loadDetail(followRef.current)
    })
    return () => es.close()
  }, [loadDetail, refreshSide, openConversation])

  useEffect(() => { if (tab === 'Vault' && !vault) api.vaultGraph().then(setVault).catch(() => {}) }, [tab, vault])

  /* the journal for this conversation, oldest first ------------------------ */
  const turns = useMemo(() => {
    const list = runs.filter((r) => r.conversation_id === convId).slice().reverse()
    if (detail && detail.conversation_id === convId) {
      const i = list.findIndex((t) => t.id === detail.id)
      const fresh = { id: detail.id, goal: detail.goal, status: detail.status, answer: detail.answer,
        pending: detail.pending, stats: detail.stats, created_at: detail.created_at, conversation_id: detail.conversation_id }
      if (i === -1) list.push(fresh)
      else list[i] = { ...list[i], ...fresh }
    }
    return list
  }, [runs, convId, detail])

  const running = detail?.status === 'running'

  /* scrolling ------------------------------------------------------------- */
  const onScroll = () => {
    const el = scrollRef.current
    if (el && el.scrollHeight - el.scrollTop - el.clientHeight > 200) stickRef.current = false
  }

  // keep an expanding entry where it was on screen
  useLayoutEffect(() => {
    const a = anchorRef.current
    const el = scrollRef.current
    if (!a || !el) return
    const node = document.getElementById(`turn-${a.id}`)
    if (node) el.scrollTop += node.getBoundingClientRect().top - a.top
    if (!a.waitFor || detail?.id === a.id) anchorRef.current = null
  }, [openId, detail])

  // one-shot scroll requests, and following a live entry down
  useEffect(() => {
    const el = scrollRef.current
    if (!el || tab !== 'Journal') return
    const want = intentRef.current
    if (want) {
      const node = document.getElementById(`turn-${want.id}`)
      if (node) { node.scrollIntoView({ block: 'start' }); intentRef.current = null }
      return
    }
    if (stickRef.current) el.scrollTop = el.scrollHeight
  }, [turns, tab, spans.length, live])

  // the answer reveals over ~1s after arriving; keep following it if we were following
  useEffect(() => {
    const el = scrollRef.current
    const content = el?.firstElementChild
    if (!content || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(() => { if (stickRef.current) el.scrollTop = el.scrollHeight })
    ro.observe(content)
    return () => ro.disconnect()
  }, [tab, convId])

  /* actions --------------------------------------------------------------- */
  const toggleEntry = (id) => {
    stickRef.current = false
    const node = document.getElementById(`turn-${id}`)
    const next = openId === id ? null : id
    anchorRef.current = node ? { id, top: node.getBoundingClientRect().top, waitFor: !!next } : null
    setOpenId(next)
    if (next && followRef.current !== id) follow(id)
  }

  const submit = async (e) => {
    e?.preventDefault()
    const g = goal.trim()
    if (!g || sending) return
    setError(''); setTab('Journal'); setSending(true)
    try {
      // the text stays in the box until the server has accepted it, so nothing is ever lost silently
      const { run_id } = await api.start(g, convId)
      setGoal('')
      stickRef.current = true
      setOpenId(run_id)
      follow(run_id)
      refreshSide()
    } catch (err) {
      const raw = String(err.message)
      setError(raw.includes('at most 20000') ? 'That request is too long — trim it to about 20,000 characters.'
        : err.status === 401 ? 'The API token was rejected. Check SYNAPSE_API_TOKEN.'
          : err.status === 408 ? 'No answer from the Synapse API in 15s. Usually too many Synapse tabs are open (the browser allows 6 connections) — close the others, then press Run again. Your message is still here.'
            : err.status === 0 || /fetch|network/i.test(raw) ? 'Can’t reach the Synapse API. Is it running?'
              : raw.replace(/[{}"[\]]/g, '').slice(0, 180))
    } finally {
      setSending(false)
      requestAnimationFrame(() => inputRef.current?.focus())
    }
  }

  const newChat = useCallback(() => {
    setConvId(newId()); setOpenId(null); follow(null); setTab('Journal'); setNavOpen(false)
    stickRef.current = false
    requestAnimationFrame(() => inputRef.current?.focus())
  }, [follow])

  const deleteChat = useCallback(async (id) => {
    setMenuId(null)
    try {
      await api.deleteConversation(id)
      if (convId === id) newChat()
      refreshSide()
    } catch { /* silently fail */ }
  }, [convId, newChat, refreshSide])

  useEffect(() => {
    if (!menuId) return
    const close = () => setMenuId(null)
    window.addEventListener('click', close)
    return () => window.removeEventListener('click', close)
  }, [menuId])

  const openNote = (name) => {
    setTab('Vault')
    api.vaultGraph().then(setVault).catch(() => {})
    const path = name.endsWith('.md') ? name : `${name}.md`
    api.note(path).then(setNote).catch(() => setNote({ path, missing: true, content: `*No note called “${name}” in the vault yet.*` }))
  }

  const onDecided = (ok) => {
    setDetail((r) => (r ? { ...r, pending: null, status: 'running' } : r))
    setRuns((rs) => rs.map((r) => (r.id === followRef.current ? { ...r, pending: null, status: 'running' } : r)))
    if (!ok) setLive({ stage: 'respond' })
  }

  useEffect(() => {
    const onKey = (e) => {
      const k = e.key.toLowerCase()
      const typing = ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName)
        || document.activeElement?.isContentEditable
      if ((e.metaKey || e.ctrlKey) && k === 'k') { e.preventDefault(); setPaletteOpen((v) => !v) }
      else if ((e.metaKey || e.ctrlKey) && k === 'j') { e.preventDefault(); newChat() }
      else if (e.key === '/' && !typing) { e.preventDefault(); inputRef.current?.focus() }
      else if (e.key === 'Escape' && navOpen) setNavOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [newChat, navOpen])

  // composer grows with its content, up to ~8 lines
  useLayoutEffect(() => {
    const t = inputRef.current
    if (!t) return
    t.style.height = 'auto'
    t.style.height = `${Math.min(t.scrollHeight, 200)}px`
  }, [goal])

  const waiting = runs.filter((r) => r.pending)
  const groups = useMemo(() => {
    const g = []
    convos.forEach((c) => {
      const label = dayLabel(c.updated_at)
      const last = g[g.length - 1]
      if (last && last[0] === label) last[1].push(c)
      else g.push([label, [c]])
    })
    return g
  }, [convos])

  const paletteActions = useMemo(() => [
    { id: 'new', label: 'New conversation', group: 'Action', run: newChat },
    ...TABS.map((name) => ({ id: `tab-${name}`, label: `Go to ${name}`, group: 'View', run: () => setTab(name) })),
    ...waiting.map((r) => ({ id: `appr-${r.id}`, label: `Sign: ${r.goal}`, group: 'Waiting', dot: 'waiting',
      run: () => openConversation(r.conversation_id, runs, r.id) })),
    ...convos.map((c) => ({ id: `conv-${c.id}`, label: c.title, group: dayLabel(c.updated_at),
      run: () => openConversation(c.id, runs) })),
    ...runs.map((r) => ({ id: `run-${r.id}`, label: r.goal, group: 'Entry', dot: r.pending ? 'waiting' : undefined,
      run: () => openConversation(r.conversation_id, runs, r.id) })),
  ], [convos, runs, waiting, newChat, openConversation])

  // this page's own bundle vs the one the server now serves (only meaningful for the built dashboard on :8000)
  const myBuild = typeof document !== 'undefined' && document.querySelector('script[src*="/assets/index-"]')?.getAttribute('src')?.split('/').pop()
  const staleUi = !!(myBuild && health?.ui_build && myBuild !== health.ui_build)
  const servers = Object.entries(health?.servers || {}).filter(([, v]) => v !== 'up').map(([k]) => k)
  const inConvo = runs.filter((r) => r.conversation_id === convId).slice().reverse()

  /* render ---------------------------------------------------------------- */
  if (locked) return <Unlock />
  return (
    <div className="h-full flex">
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:z-[60] focus:top-2 focus:left-2
        focus:px-3 focus:py-1.5 focus:bg-card focus:border focus:border-ink focus:text-ink">Skip to content</a>

      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} actions={paletteActions} />

      {navOpen && <div className="fixed inset-0 z-30 bg-black/45 lg:hidden" onClick={() => setNavOpen(false)} aria-hidden="true" />}

      {/* ── index ── */}
      <aside aria-label="Conversations"
        className={`surface-dark w-[272px] shrink-0 bg-paper2 border-r border-rule flex flex-col fixed inset-y-0 left-0 z-40
          transition-transform duration-200 lg:static lg:translate-x-0 ${navOpen ? 'translate-x-0' : '-translate-x-full'}`}>
        <div className="px-5 pt-5 pb-4 border-b border-rule">
          <div className="flex items-center justify-between h-7">
            <Wordmark size={19} live={running} />
            <span className="label flex items-center gap-1.5">
              <Led state={running ? 'running' : health?.ok ? 'ok' : health ? 'failed' : 'idle'} />
              <span className={running ? '!text-signalink' : ''}>{running ? 'working' : health?.ok ? 'online' : health ? 'offline' : '…'}</span>
            </span>
          </div>
          <button type="button" onClick={newChat} className="btn-ink w-full justify-between mt-5">
            <span className="inline-flex items-center gap-2"><span aria-hidden="true" className="text-[13px] leading-none">+</span>New conversation</span>
            <kbd className="num text-[9.5px] opacity-60">⌘J</kbd>
          </button>
        </div>

        <nav aria-label="Past conversations" className="flex-1 overflow-auto px-3 pt-4 pb-4">
          {convos.length === 0 && <p className="px-2 text-[13px] text-muted">Nothing here yet.</p>}
          {groups.map(([label, items]) => (
            <div key={label} className="mb-5">
              <div className="label px-2 pb-1.5">{label}</div>
              <ul>
                {items.map((c) => {
                  const open = c.id === convId
                  return (
                    <li key={c.id} className="group/conv relative">
                      <button type="button" onClick={() => openConversation(c.id, runs)} aria-current={open ? 'true' : undefined}
                        className={`w-full text-left pl-2.5 pr-7 py-2 border-l-2 transition-colors ${
                          open ? 'border-signal bg-card' : 'border-transparent hover:bg-paper3/60'}`}>
                        <span className={`block text-[13.5px] leading-snug line-clamp-2 ${open ? 'text-ink' : 'text-ink2'}`}>
                          {c.pending && <span className="inline-block align-middle mr-1.5 text-signal" title="needs your signature"><Glyph state="waiting" size={8} /></span>}
                          {c.title}
                        </span>
                        <span className="num text-[9px] text-faint">{c.turns} {c.turns === 1 ? 'run' : 'runs'}</span>
                      </button>
                      <button type="button" onClick={(e) => { e.stopPropagation(); setMenuId(menuId === c.id ? null : c.id) }}
                        className="absolute right-1 top-2 p-1 font-mono text-[14px] leading-none text-faint hover:text-ink
                          opacity-0 group-hover/conv:opacity-100 focus:opacity-100 transition-opacity"
                        aria-label="Chat options">⋮</button>
                      {menuId === c.id && (
                        <div className="absolute right-1 top-8 z-50 bg-card border border-rule2 py-1 shadow-lg min-w-[120px]"
                          onClick={(e) => e.stopPropagation()}>
                          <button type="button" onClick={() => deleteChat(c.id)}
                            className="w-full text-left px-3 py-1.5 text-[12.5px] text-ink2 hover:bg-paper3 hover:text-alert font-mono">
                            Delete
                          </button>
                        </div>
                      )}
                      {open && inConvo.length > 1 && (
                        <ol className="ml-[11px] border-l border-rule2 my-1">
                          {inConvo.map((r, i) => (
                            <li key={r.id}>
                              <button type="button" onClick={() => openConversation(c.id, runs, r.id)}
                                className={`w-full text-left pl-3 pr-1 py-1 flex items-baseline gap-2 text-[12.5px] hover:text-ink ${
                                  openId === r.id ? 'text-ink' : 'text-muted'}`}>
                                <span className={`num text-[9px] w-4 shrink-0 ${openId === r.id ? 'text-signalink' : 'text-faint'}`}>{String(i + 1).padStart(2, '0')}</span>
                                <span className="truncate">{r.goal}</span>
                              </button>
                            </li>
                          ))}
                        </ol>
                      )}
                    </li>
                  )
                })}
              </ul>
            </div>
          ))}
        </nav>

        <footer className="px-5 pt-3 pb-4 border-t border-rule">
          <dl className="grid grid-cols-[3.8rem_minmax(0,1fr)] gap-y-1 font-mono text-[10px]">
            <dt className="text-faint uppercase tracking-[0.1em]">model</dt><dd className="text-ink2 truncate">{health?.ok ? health.model : health ? 'API offline' : 'connecting…'}</dd>
            {health?.ok && <>
              <dt className="text-faint uppercase tracking-[0.1em]">engine</dt><dd className="text-ink2">{health.local ? 'local · ollama' : `${health.provider}${health.fallback ? ' + local fallback' : ''}`}</dd>
              <dt className="text-faint uppercase tracking-[0.1em]">mcp</dt><dd className="text-ink2 num">{health.tools} tools</dd>
              <dt className="text-faint uppercase tracking-[0.1em]">vault</dt><dd className="text-ink2 num">{health.notes ?? 0} notes</dd>
            </>}
          </dl>
          {servers.length > 0 && <div className="mt-1.5 font-mono text-[10px] text-alert">{servers.join(', ')} down</div>}
          {health?.restart_needed?.length > 0 && (
            <div role="alert" className="mt-2 border-l-2 border-signal pl-2 text-[11.5px] leading-snug text-ink">
              Code changed since the API started — <b>restart the API</b> to use it.
              <div className="font-mono text-[9.5px] text-muted mt-0.5 break-all">{health.restart_needed.slice(0, 4).join(', ')}</div>
            </div>
          )}
          {staleUi && (
            <div role="alert" className="mt-2 border-l-2 border-signal pl-2 text-[11.5px] leading-snug text-ink">
              A newer dashboard is available — <button type="button" className="underline" onClick={() => location.reload()}>reload this page</button>.
            </div>
          )}
          <div className="pt-3.5"><Appearance /></div>
        </footer>
      </aside>

      {/* ── page ── */}
      <main className="flex-1 min-w-0 flex flex-col">
        <header className={`relative flex items-center gap-4 px-4 sm:px-8 border-b border-rule bg-paper`}>
          <button type="button" onClick={() => setNavOpen(true)} className="btn-text lg:hidden py-3" aria-label="Open conversations">
            Index
          </button>
          <div role="tablist" aria-label="Views" className="flex items-center gap-6 overflow-x-auto">
            {TABS.map((name, i) => (
              <button key={name} type="button" role="tab" id={`tab-${name}`} aria-selected={tab === name}
                aria-controls="main" onClick={() => setTab(name)}
                className={`relative py-[15px] font-mono font-medium text-[10px] uppercase tracking-[0.1em] whitespace-nowrap transition-colors ${
                  tab === name ? 'text-ink' : 'text-muted hover:text-ink'}`}>
                <span className={`mr-1.5 ${tab === name ? 'text-signalink' : 'text-faint'}`} aria-hidden="true">{String(i + 1).padStart(2, '0')}</span>{name}
                {name === 'Agents' && running && <span className="absolute -right-2.5 top-[14px] w-[5px] h-[5px] bg-signal animate-blink" aria-hidden="true" />}
                {tab === name && <span className="absolute left-0 right-0 -bottom-px h-[2px] bg-signal" aria-hidden="true" />}
              </button>
            ))}
          </div>
          <div className="ml-auto flex items-center gap-4 shrink-0">
            {running && detail && (
              <span className="hidden md:inline-flex items-center gap-2 font-mono text-[10px] text-signalink">
                <Led state="running" /><span className="uppercase tracking-[0.1em]">run {detail.id.slice(0, 6)}</span>
              </span>
            )}
            {waiting.length > 0 && (
              <button type="button" onClick={() => openConversation(waiting[0].conversation_id, runs, waiting[0].id)}
                className="btn-signal">
                <Glyph state="waiting" size={8} /> {waiting.length} to sign
              </button>
            )}
            <button type="button" onClick={() => setPaletteOpen(true)} aria-label="Open command palette"
              className="hidden sm:inline-flex items-center gap-2.5 border border-rule2 hover:border-ink2 transition-colors pl-2.5 pr-1.5 py-1 text-[12.5px] text-muted">
              Search<kbd className="num text-[9px] text-ink2 border border-rule2 px-1 py-px">⌘K</kbd>
            </button>
          </div>
          {running && <span className="sweep absolute left-0 right-0 -bottom-px h-px" aria-hidden="true" />}
        </header>

        {tab === 'Journal' ? (
          <div id="main" role="tabpanel" aria-labelledby="tab-Journal" tabIndex={-1} className="flex-1 min-h-0 flex outline-none">
            <div ref={scrollRef} onScroll={onScroll} className="flex-1 min-w-0 overflow-auto">
              <div className="max-w-[760px] mx-auto px-5 sm:px-10 pt-10 pb-10 min-h-full">
                {turns.length === 0 ? (
                  <EmptyJournal health={health} onPick={(s) => { setGoal(s); inputRef.current?.focus() }} />
                ) : (
                  <>
                    <div className="mb-10 flex items-baseline gap-3 border-b border-rule2 pb-2.5">
                      <span className="label !text-ink">01 · Journal</span>
                      <span className="display text-[20px] leading-none">{dayLabel(turns[0].created_at)}</span>
                      <span className="label ml-auto num">{turns.length} {turns.length === 1 ? 'run' : 'runs'}</span>
                    </div>
                    <div className="relative">
                      <div className={`axon ${running ? 'axon-live' : ''}`} style={{ top: 16, bottom: 56 }} aria-hidden="true" />
                      {turns.map((t, i) => (
                        <Entry key={t.id} turn={t} index={i + 1}
                          detail={detail?.id === t.id ? detail : null}
                          selected={openId === t.id}
                          justFinished={finishedId === t.id}
                          live={detail?.id === t.id ? live : null}
                          draft={draft?.runId === t.id ? draft : null}
                          onSelect={toggleEntry} onNote={openNote} onDecided={onDecided} />
                      ))}
                    </div>
                  </>
                )}
              </div>
            </div>
            <aside aria-label="Pipeline and trace" className={`${turns.length ? 'xl:block' : ''} hidden w-[300px] shrink-0 border-l border-rule bg-paper2 px-6 pt-10 pb-6 min-h-0`}>
              <Margin run={detail} spans={spans} live={live} />
            </aside>
          </div>
        ) : (
          <div id="main" role="tabpanel" aria-labelledby={`tab-${tab}`} tabIndex={-1}
            className="flex-1 min-h-0 overflow-auto px-5 sm:px-10 py-8 outline-none">
            {tab === 'Agents' && <Agents run={detail} spans={spans} live={live} />}
            {tab === 'Vault' && <Vault data={vault} note={note} onClose={() => setNote(null)}
              onDeleted={() => { setNote(null); api.vaultGraph().then(setVault).catch(() => {}); api.health().then(setHealth).catch(() => {}) }}
              onPick={(p) => api.note(p).then(setNote).catch(() => {})} />}
            {tab === 'Memory' && <Memory />}
            {tab === 'Tools' && <Tools />}
            {tab === 'Evals' && <Evals />}
          </div>
        )}

        {/* ── composer ── */}
        <form onSubmit={submit} className="px-4 sm:px-8 pb-4 pt-2">
          <div className="max-w-[760px] mx-auto">
            {error && <p role="alert" className="text-[13.5px] text-ink border-l-2 border-alert pl-2 mb-1.5">{error}</p>}
            <div className={`ticks flex items-end gap-3 bg-card border transition-colors px-4 py-2.5 ${
              running ? 'border-signal/60' : 'border-rule2 focus-within:border-ink2'}`}>
              <span aria-hidden="true" className="font-mono text-[14px] text-signal pb-[5px] select-none">›</span>
              <label htmlFor="goal-input" className="sr-only">Message Synapse</label>
              <textarea id="goal-input" ref={inputRef} rows={1} value={goal}
                onChange={(e) => setGoal(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); submit() }
                }}
                placeholder={turns.length ? 'Follow up, or give it a new goal…' : 'Ask a question, or give it a goal…'}
                className="flex-1 resize-none bg-transparent outline-none focus-visible:outline-none text-[15.5px] leading-[1.5] text-ink py-1
                  placeholder:text-faint max-h-[200px]" />
              <ModelPicker health={health} onChanged={() => api.health().then(setHealth).catch(() => {})} />
              <button type="submit" disabled={!goal.trim() || sending} className="btn-ink shrink-0">
                {sending ? 'Sending…' : <>Run <span aria-hidden="true" className="opacity-60">↵</span></>}
              </button>
            </div>
            <div className="hidden sm:flex gap-5 mt-2 label !text-faint" aria-hidden="true">
              <span>↵ run</span><span>⇧↵ new line</span><span>⌘K search</span><span>⌘J new</span>
              <span className="ml-auto normal-case tracking-normal font-mono">crews: /study /decide /review /research /notes</span>
            </div>
          </div>
        </form>
      </main>
    </div>
  )
}
