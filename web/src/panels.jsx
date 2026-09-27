import { useEffect, useMemo, useRef, useState } from 'react'
import { css, darkNow, ink } from './theme'

/* ── primitives ───────────────────────────────────────────────────────────── */

/** Status colours as token names. Amber = current is flowing, or it is waiting on you.
 *  Done is ink; failed is oxide red. Every state also has its own glyph. */
const TOKEN = {
  running: 'signal', ok: 'ink2', success: 'ink2', waiting: 'signalink', awaiting_approval: 'signalink',
  failed: 'alert', error: 'alert', rejected: 'alert', blocked: 'alert', idle: 'faint', skipped: 'faint',
}
export const C = Object.fromEntries(Object.entries(TOKEN).map(([k, v]) => [k, css(v)]))
const BAD = ['failed', 'error', 'rejected', 'blocked']
const WORD = { awaiting_approval: 'needs you', waiting: 'needs you', ok: 'done', success: 'done', running: 'working' }

export const fmt = (ms) =>
  ms == null ? '—' : ms > 60000 ? `${Math.floor(ms / 60000)}m ${Math.round((ms % 60000) / 1000)}s`
    : ms > 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`

export const Dot = ({ c, size = 6 }) => (
  <span className="inline-block shrink-0" style={{ width: size, height: size, background: c }} aria-hidden="true" />
)

/** Drawn, not typed: unicode status glyphs fall back to whatever font the OS has. */
export function Glyph({ state = 'idle', size = 9 }) {
  const s = state === 'success' ? 'ok' : state === 'awaiting_approval' ? 'waiting' : BAD.includes(state) && state !== 'blocked' ? 'failed' : state
  return (
    <svg width={size} height={size} viewBox="0 0 10 10" aria-hidden="true" className={`shrink-0 ${s === 'running' ? 'animate-blink' : ''}`}
      fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="square">
      {s === 'running' && <rect x="1.5" y="1.5" width="7" height="7" fill="currentColor" stroke="none" />}
      {s === 'ok' && <path d="M1.5 5.5 4 8 8.8 2" />}
      {s === 'waiting' && <><rect x="1.5" y="1.5" width="7" height="7" /><path d="M1.5 1.5h7l-7 7z" fill="currentColor" stroke="none" /></>}
      {s === 'failed' && <path d="M2 2l6 6M8 2 2 8" />}
      {s === 'blocked' && <><circle cx="5" cy="5" r="3.6" /><path d="M2.5 7.5l5-5" /></>}
      {s === 'skipped' && <path d="M2 5h6" />}
      {!['running', 'ok', 'waiting', 'failed', 'blocked', 'skipped'].includes(s) && <rect x="2" y="2" width="6" height="6" />}
    </svg>
  )
}

/** The square status LED (styles in index.css). */
export const Led = ({ state = 'idle', className = '' }) => <span aria-hidden="true" className={`led ${className}`} data-s={state} />

/** A status as a readout: glyph + mono word. */
export function Mark({ state = 'idle', children }) {
  return (
    <span className="inline-flex items-center gap-1.5 font-mono font-medium text-[9.5px] uppercase tracking-[0.08em] whitespace-nowrap"
      style={{ color: C[state] || C.idle }}>
      <Glyph state={state} />
      {children || WORD[state] || state}
    </span>
  )
}

export function Elapsed({ from }) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const i = setInterval(() => setNow(Date.now()), 250)
    return () => clearInterval(i)
  }, [])
  return <span className="num text-[10px] text-muted">{fmt(Math.max(0, now - from * 1000))}</span>
}

const SANS = '"Archivo Variable", system-ui, sans-serif'

/* ── the graph's stages ───────────────────────────────────────────────────── */

export const STAGES = [
  ['recall', 'memory'], ['triage', 'route'], ['plan', 'planner'],
  ['step', 'agents'], ['verify', 'read-back'], ['respond', 'answer'], ['memorize', 'persist'],
]

export function stageStates(run, spans, live) {
  const running = run?.status === 'running'
  return STAGES.map(([name]) => {
    if (running && (live?.stage === name || (name === 'step' && live?.stage === 'crew'))) return 'running'
    if (run?.status === 'awaiting_approval' && name === 'step') return 'waiting'
    const hit = spans.filter((s) => s.name === name)
    if (!hit.length) return 'idle'
    return hit.some((s) => s.attrs?.error || s.attrs?.status === 'failed') ? 'failed' : 'ok'
  })
}

/* ── agents ───────────────────────────────────────────────────────────────── */

/** Agents are a presentation grouping over tools and graph nodes. Give an agent a `tools` list and
 *  it lights up whenever one of those tools runs. Lanes group them by what kind of work they do. */
export const LANES = [
  { id: 'reason', name: 'Reasoning', note: 'decide what to do, check it was done' },
  { id: 'know', name: 'Knowledge', note: 'find, read and write what is known' },
  { id: 'act', name: 'Action', note: 'change things in the world' },
]

export const ROSTER = [
  { id: 'planner', lane: 'reason', name: 'Planner', role: 'turns the goal into a validated plan', nodes: ['plan', 'replan', 'triage'] },
  { id: 'verifier', lane: 'reason', name: 'Verifier', role: 'reads writes back, checks hashes', nodes: ['verify'] },
  { id: 'researcher', lane: 'know', name: 'Researcher', role: 'searches the web and reads pages', nodes: [],
    tools: ['web_search', 'fetch_url', 'research'] },
  { id: 'librarian', lane: 'know', name: 'Librarian', role: 'searches the vault, keeps memory', nodes: ['recall', 'memorize'],
    tools: ['search_notes', 'gather_notes', 'read_note', 'list_notes', 'notion_search', 'notion_read_page'] },
  { id: 'scribe', lane: 'know', name: 'Scribe', role: 'writes content and saves notes', nodes: [],
    tools: ['create_note', 'append_to_note', 'update_note', 'notion_create_page', 'notion_append'] },
  { id: 'organiser', lane: 'act', name: 'Organiser', role: 'moves, tags and links notes', nodes: [],
    tools: ['move_note', 'add_tags', 'replace_section', 'related_notes', 'vault_stats', 'list_folders', 'delete_note'] },
  { id: 'comms', lane: 'act', name: 'Comms', role: 'email, messages, notifications', nodes: [],
    tools: ['gmail_send', 'gmail_create_draft', 'gmail_search', 'gmail_read_thread', 'run_workflow', 'notify', 'list_workflows'] },
  { id: 'scheduler', lane: 'act', name: 'Scheduler', role: 'calendar, tasks, timed goals', nodes: [],
    tools: ['calendar_create_event', 'calendar_list_events', 'schedule_task', 'list_schedules', 'cancel_schedule',
            'add_task', 'list_tasks', 'complete_task', 'now', 'weather'] },
  // CrewAI teams (synapse/crew.py). They read with tools; saving/sending stays with the agents above.
  { id: 'crew-research', lane: 'crew', name: 'Research crew', cmd: '/research', role: 'deep, sourced reports and comparisons',
    team: 'researcher → analyst → writer', uses: 'web', nodes: [] },
  { id: 'crew-study', lane: 'crew', name: 'Study crew', cmd: '/study', role: 'learning path, explanations, quiz',
    team: 'curriculum → tutor → quizmaster', uses: 'web', nodes: [] },
  { id: 'crew-review', lane: 'crew', name: 'Editor crew', cmd: '/review', role: 'improves a draft you wrote',
    team: 'critic → editor', nodes: [] },
  { id: 'crew-vault', lane: 'crew', name: 'Archivist crew', cmd: '/notes', role: 'answers from or merges your notes',
    team: 'archivist → synthesiser', uses: 'vault', nodes: [] },
  { id: 'crew-decide', lane: 'crew', name: 'Decision crew', cmd: '/decide', role: 'weighs a choice from both sides',
    team: 'advocate → skeptic → judge', uses: 'web', nodes: [] },
]

const OWNER = Object.fromEntries(ROSTER.flatMap((a) => (a.tools || []).map((t) => [t, a.id])))
const NAME = Object.fromEntries(ROSTER.map((a) => [a.id, a.name]))

function ownerOf(spanName, attrs = {}) {
  if (spanName !== 'step') return ROSTER.find((r) => r.nodes.includes(spanName))?.id
  if (attrs.kind === 'crew') return `crew-${attrs.crew || 'research'}`
  if (attrs.kind === 'generate') return 'scribe'
  const t = attrs.tool || (attrs.tool_calls || [])[0]?.tool
  return OWNER[t] || 'planner'
}

const spanState = (a) => (a.error || a.status === 'failed' ? 'failed' : a.status === 'blocked' ? 'blocked'
  : a.status === 'rejected' ? 'rejected' : 'ok')

export function useAgentModel(run, spans, live) {
  return useMemo(() => {
    const work = {}
    const put = (id, patch) => { work[id] = { calls: 0, ms: 0, tokens: 0, tools: [], ...work[id], ...patch } }
    spans.forEach((s) => {
      const a = s.attrs || {}
      const id = ownerOf(s.name, a)
      if (!id) return
      const prev = work[id] || { calls: 0, ms: 0, tokens: 0, tools: [] }
      put(id, {
        calls: prev.calls + 1, ms: prev.ms + (s.latency_ms || 0),
        tools: [...new Set([...prev.tools, a.tool, ...(a.tool_calls || []).map((c) => c.tool)].filter(Boolean))],
        tokens: prev.tokens + (a.llm || []).reduce((n, c) => n + (c.output_tokens || 0), 0),
        last: a.description || a.tool || s.name,
        state: spanState(a) === 'ok' ? 'ok' : 'failed',
      })
    })
    if (run?.status === 'running' && live?.stage) {
      const id = live.stage === 'crew' || live.kind === 'crew' ? `crew-${live.crew || 'research'}`
        : ownerOf(live.stage === 'step' ? 'step' : live.stage, live)
      if (id) put(id, { state: 'running', last: live.description || live.agent || live.stage, detail: live.tool })
    }
    if (run?.status === 'awaiting_approval') {
      put(OWNER[run.pending?.tool] || 'planner', { state: 'waiting', last: run.pending?.description, detail: run.pending?.tool })
    }
    return work
  }, [run, spans, live])
}

/* wire state from agent states: live beats used beats idle */
const wireOf = (states) => (states.some((s) => s === 'running' || s === 'waiting') ? 'live'
  : states.some((s) => s && s !== 'idle') ? 'used' : 'idle')

function StageStrip({ run, spans, live }) {
  const states = stageStates(run, spans, live)
  return (
    <ol className="flex items-stretch overflow-x-auto">
      {STAGES.map(([name, sub], i) => {
        const s = states[i]
        return (
          <li key={name} className="flex items-center min-w-0 flex-1">
            <div className={`flex-1 min-w-[84px] px-2.5 py-2 border ${s === 'running' || s === 'waiting'
              ? 'border-signal bg-signal/[0.07]' : s === 'idle' ? 'border-rule' : BAD.includes(s) ? 'border-alert/70' : 'border-rule2'}`}>
              <div className="flex items-center gap-1.5">
                <Led state={s} />
                <span className={`font-mono text-[10.5px] ${s === 'idle' ? 'text-faint' : 'text-ink'}`}>{name}</span>
              </div>
              <div className="label !text-[8.5px] mt-0.5 truncate">{sub}</div>
            </div>
            {i < STAGES.length - 1 && <span className="wire wire-h h-px w-3 shrink-0"
              data-s={states[i + 1] === 'running' ? 'live' : states[i + 1] !== 'idle' ? 'used' : 'idle'} aria-hidden="true" />}
          </li>
        )
      })}
    </ol>
  )
}

function AgentCard({ a, w, last, spine }) {
  const s = w.state || 'idle'
  const on = s === 'running' || s === 'waiting'
  return (
    <li className="relative pl-6 pt-3">
      <span className={`wire wire-v absolute left-[5px] top-0 w-px ${last ? 'h-[31px]' : 'h-full'}`} data-s={spine} aria-hidden="true" />
      <span className="wire wire-h absolute left-[5px] top-[31px] w-[19px] h-px" data-s={on ? 'live' : s !== 'idle' ? 'used' : 'idle'} aria-hidden="true" />
      <div className={`relative px-3 py-2.5 border transition-colors duration-300 ${on ? 'border-signal bg-signal/[0.06]'
        : BAD.includes(s) ? 'border-alert/60 bg-card' : s === 'idle' ? 'border-rule bg-card/60' : 'border-rule2 bg-card'}`}>
        <div className="flex items-center gap-2">
          <Led state={s} />
          <span className={`text-[14px] font-semibold tracking-[-0.005em] whitespace-nowrap ${s === 'idle' ? 'text-ink2' : 'text-ink'}`}>{a.name}</span>
          {s !== 'idle' && <span className="ml-auto"><Mark state={s} /></span>}
        </div>
        <p className="text-[12.5px] text-muted leading-snug mt-0.5">{a.role}</p>
        {(w.last || w.calls > 0) && (
          <div className="mt-2 pt-1.5 border-t border-rule flex items-baseline gap-2 min-w-0">
            <span className={`font-mono text-[10.5px] truncate ${on ? 'text-signalink' : 'text-ink2'}`}>{w.last || '—'}</span>
            {w.calls > 0 && <span className="ml-auto num text-[9.5px] text-muted shrink-0">{w.calls}× · {fmt(w.ms)}</span>}
          </div>
        )}
      </div>
    </li>
  )
}

function Lane({ lane, work, through }) {
  const agents = ROSTER.filter((a) => a.lane === lane.id)
  const st = agents.map((a) => work[a.id]?.state)
  // current flows down the spine only as far as the lowest agent that is working
  const deepestLive = st.map((s, i) => (s === 'running' || s === 'waiting' ? i : -1)).reduce((m, i) => Math.max(m, i), -1)
  const deepestUsed = st.map((s, i) => (s && s !== 'idle' ? i : -1)).reduce((m, i) => Math.max(m, i), -1)
  const seg = (i) => (i <= deepestLive ? 'live' : i <= deepestUsed ? 'used' : 'idle')
  return (
    <section aria-label={`${lane.name} agents`} className="relative min-w-0">
      {/* the first lane's spine runs on down to the crews row below */}
      {through && <span className="wire wire-v absolute left-[5px] top-0 bottom-0 w-px" data-s={through} aria-hidden="true" />}
      <div className="relative pl-6 pt-4 pb-0.5">
        <span className="wire wire-v absolute left-[5px] top-0 bottom-0 w-px" data-s={seg(0)} aria-hidden="true" />
        <span className="absolute left-[2px] top-0 w-[7px] h-[7px] -translate-y-1/2 bg-paper border border-rule2" aria-hidden="true" />
        <span className="label !text-ink">{lane.name}</span>
        <p className="text-[11.5px] text-muted leading-snug">{lane.note}</p>
      </div>
      <ol>{agents.map((a, i) => <AgentCard key={a.id} a={a} w={work[a.id] || {}} last={i === agents.length - 1} spine={seg(i)} />)}</ol>
    </section>
  )
}

/** The CrewAI teams: a row under the lanes, fed by the same state bus. */
function Crews({ work }) {
  const crews = ROSTER.filter((a) => a.lane === 'crew')
  const st = crews.map((a) => work[a.id]?.state || 'idle')
  const row = wireOf(st)
  return (
    <section aria-label="CrewAI crews" className="mt-7">
      <div className="relative pl-6 pb-2">
        <span className="wire wire-v absolute left-[5px] -top-7 bottom-0 w-px" data-s={row} aria-hidden="true" />
        <span className="label !text-ink">Crews</span>
        <span className="label ml-2 !text-faint">CrewAI · teams of specialists, with read-only tools · type a command to call one</span>
      </div>
      <div className="relative">
        <span className="wire wire-h absolute left-[5px] right-[10%] top-0 h-px" data-s={row} aria-hidden="true" />
        <ol className="grid sm:grid-cols-2 lg:grid-cols-5 gap-3 pt-3 pl-[5px]">
          {crews.map((a, i) => {
            const s = st[i]
            const on = s === 'running' || s === 'waiting'
            const w = work[a.id] || {}
            return (
              <li key={a.id} className="relative pt-3">
                <span className="wire wire-v absolute left-3 -top-3 h-6 w-px" data-s={on ? 'live' : s !== 'idle' ? 'used' : 'idle'} aria-hidden="true" />
                <div className={`relative h-full px-3 py-2.5 border transition-colors duration-300 ${on ? 'border-signal bg-signal/[0.06]'
                  : BAD.includes(s) ? 'border-alert/60 bg-card' : s === 'idle' ? 'border-rule bg-card/60' : 'border-rule2 bg-card'}`}>
                  <div className="flex items-center gap-2">
                    <Led state={s} />
                    <span className={`text-[13.5px] font-semibold whitespace-nowrap ${s === 'idle' ? 'text-ink2' : 'text-ink'}`}>{a.name}</span>
                  </div>
                  <p className="text-[12px] text-muted leading-snug mt-0.5">{a.role}</p>
                  <p className="font-mono text-[9.5px] text-faint mt-1.5 leading-snug">{a.team}</p>
                  <div className="mt-2 pt-1.5 border-t border-rule flex items-baseline gap-2 min-w-0">
                    <code className={`font-mono text-[10.5px] ${on ? 'text-signalink' : 'text-ink2'}`}>{on ? (w.last || 'working') : a.cmd}</code>
                    {a.uses && <span className="label !text-[8.5px] !text-faint whitespace-nowrap">{a.uses} tools</span>}
                    {w.calls > 0 && <span className="ml-auto num text-[9.5px] text-muted shrink-0">{fmt(w.ms)}</span>}
                  </div>
                </div>
              </li>
            )
          })}
        </ol>
      </div>
    </section>
  )
}

function Handoffs({ spans }) {
  const rows = spans.map((s) => {
    const a = s.attrs || {}
    return { id: s.seq ?? s.span_id, who: NAME[ownerOf(s.name, a)] || 'Runtime', node: s.name, what: a.description || a.tool || '',
      tool: a.tool, ms: s.latency_ms, st: spanState(a) }
  })
  if (!rows.length) return <p className="text-[13px] text-muted pt-2">No handoffs yet.</p>
  return (
    <ol className="relative">
      {rows.map((r, i) => (
        <li key={`${r.id}-${i}`} className="grid grid-cols-[1.4rem_minmax(0,1fr)_auto] gap-x-2 py-2 hair fade-in">
          <span className="num text-[9.5px] text-faint pt-[3px]">{String(i + 1).padStart(2, '0')}</span>
          <div className="min-w-0">
            <div className="flex items-baseline gap-1.5 min-w-0">
              <span className="text-[13px] font-semibold text-ink shrink-0">{r.who}</span>
              <span className="font-mono text-[10px] text-muted truncate">{r.node}{r.tool ? ` › ${r.tool}` : ''}</span>
            </div>
            {r.what && r.what !== r.tool && <div className="text-[12px] text-muted leading-snug truncate">{r.what}</div>}
          </div>
          <div className="text-right">
            <Mark state={r.st}>{fmt(r.ms)}</Mark>
          </div>
        </li>
      ))}
    </ol>
  )
}

function McpBoundary({ spans }) {
  const calls = []
  spans.forEach((s) => {
    const a = s.attrs || {}
    const list = a.tool_calls?.length ? a.tool_calls : a.tool_call ? [a.tool_call]
      : a.tool ? [{ tool: a.tool, ok: !a.error && a.status !== 'failed' }] : []
    list.forEach((c) => calls.push({ tool: c.tool, st: c.ok === false ? 'failed' : a.status === 'blocked' ? 'blocked' : 'ok',
      ms: c.latency_ms ?? s.latency_ms }))
  })
  return (
    <div className="border-t border-dashed border-rule2 mt-6 pt-3">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="label !text-ink">MCP boundary</span>
        <span className="text-[12px] text-muted">every call schema-validated, time-limited and risk-classed</span>
        <span className="ml-auto label num">{calls.length} {calls.length === 1 ? 'call' : 'calls'}</span>
      </div>
      {calls.length > 0 ? (
        <ul className="mt-2.5 flex flex-wrap gap-1.5">
          {calls.map((c, i) => (
            <li key={i} className={`inline-flex items-center gap-2 px-2 py-1 border bg-card font-mono text-[10.5px] ${
              BAD.includes(c.st) ? 'border-alert/60 text-ink' : 'border-rule2 text-ink2'}`}>
              <span style={{ color: C[c.st] }}><Glyph state={c.st} size={8} /></span>{c.tool}
              {c.ms != null && <span className="text-faint">{fmt(c.ms)}</span>}
            </li>
          ))}
        </ul>
      ) : <p className="mt-1.5 text-[12.5px] text-faint">No tool calls in this run.</p>}
    </div>
  )
}

export function Agents({ run, spans, live }) {
  const work = useAgentModel(run, spans, live)
  const states = ROSTER.map((a) => work[a.id]?.state)
  const bus = wireOf(states)
  const engaged = states.filter((s) => s && s !== 'idle').length
  const tokens = Object.values(work).reduce((n, w) => n + (w.tokens || 0), 0)
  const status = run?.status
  const hot = status === 'running' || status === 'awaiting_approval'

  return (
    <div className="max-w-[1320px] mx-auto">
      <header className="flex flex-wrap items-end gap-x-8 gap-y-3 mb-6">
        <div>
          <div className="label">02 · Agents</div>
          <h1 className="display text-[34px] leading-[1.05] mt-1.5">Topology</h1>
        </div>
        <dl className="flex gap-x-7 gap-y-2 flex-wrap pb-1">
          {[['engaged', `${engaged}/${ROSTER.length}`], ['model calls', run?.stats?.llm_calls ?? '—'],
            ['tool calls', run?.stats?.tool_calls ?? '—'], ['tokens out', tokens || '—'], ['wall time', fmt(run?.stats?.latency_ms)]]
            .map(([k, v]) => (
              <div key={k}><dt className="label">{k}</dt><dd className="num text-[17px] text-ink mt-0.5">{v}</dd></div>
            ))}
        </dl>
      </header>

      <div className="grid xl:grid-cols-[minmax(0,1fr)_320px] gap-8">
        <figure className="panel ticks grid-paper p-5 sm:p-6 min-w-0" aria-label="Agent topology">
          {/* orchestrator */}
          <div className={`bg-card border ${hot ? 'border-signal' : 'border-rule2'} transition-colors`}>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5 border-b border-rule">
              <Led state={status === 'awaiting_approval' ? 'waiting' : status || 'idle'} />
              <span className="text-[14px] font-semibold text-ink">Orchestrator</span>
              <span className="font-mono text-[10.5px] text-muted">LangGraph · checkpointed state machine</span>
              <span className="ml-auto"><Mark state={status === 'awaiting_approval' ? 'waiting' : status || 'idle'}>
                {status ? undefined : 'no run selected'}</Mark></span>
            </div>
            <div className="p-3"><StageStrip run={run} spans={spans} live={live} /></div>
          </div>

          {/* orchestrator → state bus */}
          <div className="flex justify-center"><span className="wire wire-v w-px h-6" data-s={bus} aria-hidden="true" /></div>
          <div className="relative">
            <span className="wire wire-h block h-px w-full" data-s={bus} aria-hidden="true" />
            <span className="absolute left-1/2 -translate-x-1/2 -top-[3px] w-[7px] h-[7px] bg-paper border border-rule2" aria-hidden="true" />
            <span className="absolute right-0 top-1.5 label !text-faint !text-[8.5px]">state bus</span>
          </div>

          {/* lanes */}
          <div className="grid md:grid-cols-3 gap-x-6">
            {LANES.map((l, i) => <Lane key={l.id} lane={l} work={work}
              through={i === 0 ? wireOf(ROSTER.filter((a) => a.lane === 'crew').map((a) => work[a.id]?.state)) : undefined} />)}
          </div>
          <Crews work={work} />

          <McpBoundary spans={spans} />
          {!run && <p className="mt-4 text-[13px] text-muted">Open an entry in the journal, or start a run — agents light up
            as work moves through them.</p>}
        </figure>

        <section aria-labelledby="handoffs-h" className="min-w-0">
          <div className="flex items-baseline justify-between border-b border-rule2 pb-2">
            <h2 id="handoffs-h" className="display text-[20px] leading-none">Handoffs</h2>
            <span className="label">{spans.length} spans</span>
          </div>
          <p className="text-[12.5px] text-muted mt-2 mb-1">Who held the run, in order — every graph transition and tool
            call, from the trace.</p>
          <Handoffs spans={spans} />
        </section>
      </div>
    </div>
  )
}

/* ── vault graph ──────────────────────────────────────────────────────────── */

/** The vault as a graph. Your notes are bone; notes Synapse wrote are amber. Canvas + a tiny
 *  force simulation, no dependency. */
export function VaultGraph({ data, onPick }) {
  const ref = useRef(null)
  const st = useRef({ nodes: [], links: [], hover: null, raf: 0, drag: null })
  const [sel, setSel] = useState(null)
  // The click handler is read through a ref: it is a new function on every parent render, and having it
  // in the effect's dependencies restarted the layout several times a second while a run streamed.
  const pickRef = useRef(onPick)
  pickRef.current = onPick

  useEffect(() => {
    const canvas = ref.current
    if (!canvas || !data) return
    const prev = Object.fromEntries((st.current.nodes || []).map((n) => [n.id, n]))
    const dpr = Math.min(window.devicePixelRatio || 1, 2)
    const resize = () => {
      const r = canvas.getBoundingClientRect()
      canvas.width = r.width * dpr; canvas.height = r.height * dpr
    }
    resize()
    const W = () => canvas.width / dpr, H = () => canvas.height / dpr
    // notes already on screen keep their place when the vault is re-read; only new notes settle in
    const nodes = data.nodes.map((n, i) => (prev[n.id]
      ? { ...n, x: prev[n.id].x, y: prev[n.id].y, vx: 0, vy: 0 }
      : { ...n, x: W() / 2 + Math.cos(i * 2.4) * (40 + i * 6), y: H() / 2 + Math.sin(i * 2.4) * (40 + i * 6), vx: 0, vy: 0 }))
    const settled = nodes.length > 0 && nodes.every((n) => prev[n.id])
    const byId = Object.fromEntries(nodes.map((n) => [n.id, n]))
    const links = data.links.map((l) => ({ s: byId[l.source], t: byId[l.target] })).filter((l) => l.s && l.t)
    st.current = { ...st.current, nodes, links }
    let alpha = settled ? 0.004 : Object.keys(prev).length ? 0.3 : 1

    const draw = () => {
      const ctx = canvas.getContext('2d')
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      if (alpha > 0.004) {
        nodes.forEach((a, i) => {
          a.vx += (W() / 2 - a.x) * 0.0015; a.vy += (H() / 2 - a.y) * 0.0015
          for (let j = i + 1; j < nodes.length; j++) {
            const b = nodes[j]
            let dx = a.x - b.x, dy = a.y - b.y
            const d2 = dx * dx + dy * dy || 0.01
            if (d2 < 52900) { const f = 520 / d2; dx *= f; dy *= f; a.vx += dx; a.vy += dy; b.vx -= dx; b.vy -= dy }
          }
        })
        links.forEach(({ s, t }) => {
          const dx = t.x - s.x, dy = t.y - s.y, d = Math.hypot(dx, dy) || 1, f = (d - 90) * 0.011
          s.vx += (dx / d) * f; s.vy += (dy / d) * f; t.vx -= (dx / d) * f; t.vy -= (dy / d) * f
        })
        nodes.forEach((n) => {
          if (n === st.current.drag) return
          n.x += (n.vx *= 0.8) * alpha; n.y += (n.vy *= 0.8) * alpha
          n.x = Math.max(24, Math.min(W() - 24, n.x)); n.y = Math.max(24, Math.min(H() - 24, n.y))
        })
        alpha *= 0.986
      }
      const dark = darkNow()
      ctx.clearRect(0, 0, W(), H())
      const hov = st.current.hover
      const near = new Set(hov ? links.filter((l) => l.s === hov || l.t === hov).flatMap((l) => [l.s.id, l.t.id]) : [])
      // Obsidian-like: purple links, small glowing purple dots
      const PURPLE = (a) => `rgba(139, 108, 255, ${a})`
      links.forEach(({ s, t }) => {
        const on = hov && (s === hov || t === hov)
        ctx.strokeStyle = on ? PURPLE(0.95) : PURPLE(hov ? 0.18 : dark ? 0.5 : 0.6); ctx.lineWidth = on ? 1.4 : 1
        ctx.beginPath(); ctx.moveTo(s.x, s.y); ctx.lineTo(t.x, t.y); ctx.stroke()
      })
      nodes.forEach((n) => {
        const r = 2.6 + Math.min(n.links, 10) * 0.35
        const on = n === hov || near.has(n.id)
        ctx.globalAlpha = hov && !on ? 0.25 : 1
        ctx.shadowColor = PURPLE(1); ctx.shadowBlur = on ? 18 : dark ? 11 : 5
        ctx.fillStyle = n.synapse ? 'rgb(157, 128, 255)' : 'rgb(196, 181, 253)'   // your own notes: lighter lilac
        ctx.beginPath(); ctx.arc(n.x, n.y, r, 0, Math.PI * 2); ctx.fill()
        ctx.shadowBlur = 0
        if (on) { ctx.strokeStyle = PURPLE(0.9); ctx.lineWidth = 1.2
          ctx.beginPath(); ctx.arc(n.x, n.y, r + 3.5, 0, Math.PI * 2); ctx.stroke() }
        if (on || nodes.length <= 45) {
          ctx.fillStyle = ink(on ? 'ink' : 'muted')
          ctx.font = `${on ? 600 : 450} 12px ${SANS}`
          const label = n.path.replace(/\.md$/, '').split('/').pop().slice(0, 28)
          const flip = n.x + r + 6 + ctx.measureText(label).width > W() - 8   // keep labels inside the frame
          ctx.textAlign = flip ? 'right' : 'left'
          ctx.fillText(label, flip ? n.x - r - 6 : n.x + r + 6, n.y + 4)
          ctx.textAlign = 'left'
        }
        ctx.globalAlpha = 1
      })
      st.current.raf = requestAnimationFrame(draw)
    }
    draw()

    const pos = (e) => { const r = canvas.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top] }
    const move = (e) => {
      const [x, y] = pos(e)
      if (st.current.drag) { st.current.drag.x = x; st.current.drag.y = y; alpha = Math.max(alpha, 0.25); return }
      st.current.hover = nodes.find((n) => Math.hypot(n.x - x, n.y - y) < 12) || null
      canvas.style.cursor = st.current.hover ? 'pointer' : 'default'
    }
    const down = () => { st.current.drag = st.current.hover }
    const up = () => {
      if (st.current.drag && st.current.drag === st.current.hover) { setSel(st.current.drag.path); pickRef.current?.(st.current.drag.path) }
      st.current.drag = null
    }
    canvas.addEventListener('mousemove', move)
    canvas.addEventListener('mousedown', down)
    window.addEventListener('mouseup', up)
    window.addEventListener('resize', resize)
    return () => {
      cancelAnimationFrame(st.current.raf)
      canvas.removeEventListener('mousemove', move); canvas.removeEventListener('mousedown', down)
      window.removeEventListener('mouseup', up); window.removeEventListener('resize', resize)
    }
  }, [data])

  const empty = data && data.nodes.length === 0
  const mine = data ? data.nodes.filter((n) => n.synapse).length : 0
  return (
    <div className="relative h-full">
      <canvas ref={ref} className="w-full h-full" role="img"
        aria-label={data ? `Vault graph: ${data.nodes.length} notes, ${data.links.length} links` : 'Vault graph loading'} />
      {empty && (
        <div className="absolute inset-0 grid place-content-center text-center gap-1 px-6">
          <p className="display text-[22px]">The vault is empty.</p>
          <p className="text-[13px] text-muted">Check <code className="font-mono text-[11.5px]">SYNAPSE_VAULT_PATH</code>, then ask Synapse to save something.</p>
        </div>
      )}
      <div className="absolute bottom-0 inset-x-0 flex flex-wrap items-center gap-x-5 gap-y-1 px-4 py-2 border-t border-rule bg-card label">
        <span className="inline-flex items-center gap-1.5"><span aria-hidden="true" className="w-2 h-2 rounded-full bg-[rgb(157,128,255)] shadow-[0_0_6px_rgb(139,108,255)]" /> written by Synapse{data ? ` · ${mine}` : ''}</span>
        <span className="inline-flex items-center gap-1.5"><span aria-hidden="true" className="w-2 h-2 rounded-full bg-[rgb(196,181,253)]" /> your notes{data ? ` · ${data.nodes.length - mine}` : ''}</span>
        {data && <span className="num">{data.links.length} links</span>}
        {sel && <span className="ml-auto normal-case tracking-normal text-ink truncate">{sel}</span>}
      </div>
    </div>
  )
}
