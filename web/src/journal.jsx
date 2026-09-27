/** The journal: every exchange in a conversation, in order, as nodes on one ink line.
 *
 *  Two decisions worth knowing about:
 *  - Entries render in chronological order and the selected one expands IN PLACE. The previous
 *    design moved the selected turn to the bottom of the list, which is why clicking a turn made the
 *    page jump to the bottom.
 *  - An entry is an <article>, not a <button>. Answers contain [[wikilink]] buttons, and a button
 *    inside a button is invalid HTML that browsers repair unpredictably.                            */
import { useEffect, useState } from 'react'
import { api } from './api'
import { Markdown } from './md'
import { Elapsed, Glyph, Led, Mark, ROSTER, fmt } from './panels'
import { Axon, Trace } from './rail'

/* ── reveal ───────────────────────────────────────────────────────────────── */

/** Reveals a just-finished answer progressively. A presentation effect — the model returns the
 *  answer whole — so the work is bounded by a fixed step budget, not by the text's length. The
 *  earlier version ticked every 16ms and re-parsed the whole markdown each tick, which froze long
 *  answers ("Page Unresponsive").                                                                 */

const plural = (n, w) => `${n ?? 0} ${w}${n === 1 ? '' : 's'}`

/* ── approval ─────────────────────────────────────────────────────────────── */

function EmailPreview({ args }) {
  return (
    <div className="mt-3 bg-paper border border-rule2">
      <dl className="grid grid-cols-[4.5rem_1fr] gap-x-3 gap-y-1 px-4 py-2.5 border-b border-rule text-[13.5px]">
        <dt className="label self-center">To</dt><dd className="text-ink">{(args.to || args.attendees || []).join(', ')}</dd>
        {args.subject && <><dt className="label self-center">Subject</dt><dd className="text-ink">{args.subject}</dd></>}
      </dl>
      <div className="px-4 py-3 text-[14px] leading-relaxed text-ink2 whitespace-pre-wrap max-h-72 overflow-auto">
        {args.body || <span className="text-muted">empty body</span>}
      </div>
    </div>
  )
}

function Approval({ runId, pending, onDecided }) {
  const [comment, setComment] = useState('')
  const [busy, setBusy] = useState(false)
  const act = async (ok) => {
    setBusy(true)
    onDecided(ok)                       // clear immediately; the run resumes server-side
    try { await api.approve(runId, ok, comment) } catch { /* the stream resyncs */ }
  }
  const isMail = ['gmail_send', 'gmail_create_draft'].includes(pending.tool)
  return (
    <section aria-labelledby={`appr-${runId}`}
      className="my-5 bg-card border border-signal/70 border-l-[3px] !border-l-signal fade-in">
      <div id={`appr-${runId}`} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-5 py-2 border-b border-rule bg-signal/[0.06]">
        <Led state="waiting" /><span className="label !text-signalink">Human in the loop · needs your signature</span>
        <span className="ml-auto label">{pending.risk} risk</span>
        {pending.tainted && <span className="label !text-alert">untrusted input</span>}
      </div>
      <div className="px-5 pt-3 pb-4">
      <p className="text-[16.5px] font-semibold text-ink leading-snug">{pending.description}</p>
      {pending.reason && <p className="text-[13.5px] text-muted mt-0.5">{pending.reason}</p>}

      {isMail ? <EmailPreview args={pending.args || {}} /> : (
        <pre className="mt-3 font-mono text-[11.5px] leading-relaxed bg-paper border border-rule2 text-ink px-4 py-3 max-h-60 overflow-auto whitespace-pre-wrap">
          <span className="text-signalink">{pending.tool}</span>({JSON.stringify(pending.args, null, 2)})
        </pre>
      )}

      <label htmlFor={`note-${runId}`} className="sr-only">Note for this decision (optional)</label>
      <input id={`note-${runId}`} value={comment} onChange={(e) => setComment(e.target.value)}
        placeholder="Add a note (optional)"
        className="mt-3 w-full bg-transparent border-b border-rule2 py-1.5 text-[14px] text-ink outline-none focus:border-signal placeholder:text-faint" />
      <div className="flex gap-2 mt-4">
        <button type="button" disabled={busy} onClick={() => act(true)} className="btn-signal">
          {busy ? 'Signing…' : isMail ? 'Approve & send' : 'Approve'}
        </button>
        <button type="button" disabled={busy} onClick={() => act(false)} className="btn-line">Decline</button>
      </div>
      </div>
    </section>
  )
}

/* ── plan ─────────────────────────────────────────────────────────────────── */

function Plan({ run, live }) {
  const steps = run.plan?.steps || []
  if (!steps.length) return null
  const running = run.status === 'running'
  return (
    <section aria-label="Plan" className="my-5 panel ticks">
      <div className="flex flex-wrap items-baseline gap-x-3 px-4 py-2 border-b border-rule">
        <span className="label !text-ink">Plan</span>
        <span className="label">{steps.length} {steps.length === 1 ? 'step' : 'steps'}</span>
        {run.stats?.replans > 0 && <span className="label !text-signalink">replanned ×{run.stats.replans}</span>}
        {run.plan.intent && <span className="w-full text-[13px] text-muted">{run.plan.intent}</span>}
      </div>
      <ol className="px-4 py-2.5 space-y-2">
        {steps.map((s, i) => {
          const r = run.results?.[s.id]
          const active = running && live?.step === s.id
          const signing = run.pending && !r && i === steps.findIndex((x) => !run.results?.[x.id])
          return (
            <li key={s.id} className="grid grid-cols-[0.9rem_1.6rem_minmax(0,1fr)_auto] items-center gap-x-2 text-[14px]">
              <Led state={r?.status || (active ? 'running' : signing ? 'waiting' : 'idle')} />
              <span className="num text-[9.5px] text-faint">{String(i + 1).padStart(2, '0')}</span>
              <span className={active ? 'font-semibold text-ink' : 'text-ink2'}>{s.description}</span>
              <span className="flex items-center gap-3">
                <code className="hidden sm:inline font-mono text-[10px] text-muted">{s.tool || s.kind}</code>
                <Mark state={r?.status || (active ? 'running' : signing ? 'awaiting_approval' : 'idle')} />
              </span>
            </li>
          )
        })}
      </ol>
    </section>
  )
}

/* ── entry ────────────────────────────────────────────────────────────────── */

function Answer({ text, justFinished, onNote }) {
  // No typing replay: the text already streamed in (or arrived whole from a fast model), and replaying
  // it word by word afterwards only made a finished answer look slow.
  const shown = text
  return (
    <div className="relative">
      <Markdown text={shown} onNote={onNote} />
      {shown.length < (text || '').length && (
        <span aria-hidden="true" className="inline-block w-[8px] h-[15px] -mb-0.5 bg-signal animate-blink" />
      )}
    </div>
  )
}

/** The entry's terminal on the axon: a square LED; the open entry gets an ink frame. */
function Node({ status, pending, selected }) {
  return (
    <span className="absolute left-[-1px] top-[3px] w-[17px] h-[17px] grid place-items-center bg-paper" aria-hidden="true"
      style={{ outline: selected ? '1px solid rgb(var(--ink2))' : 'none', outlineOffset: 0 }}>
      <span className="led" data-s={pending ? 'waiting' : status} />
    </span>
  )
}

export function Entry({ turn, detail, index, selected, justFinished, live, draft, onSelect, onNote, onDecided }) {
  // `detail` is the full run (plan, spans, pending) and is only present for the selected entry.
  const r = selected && detail ? detail : turn
  const status = r.status
  const running = status === 'running'
  const [copied, setCopied] = useState(false)
  const [clamped, setClamped] = useState(true)
  const longGoal = (r.goal || '').length > 320

  return (
    <article id={`turn-${turn.id}`} aria-labelledby={`q-${turn.id}`} aria-current={selected ? 'true' : undefined}
      className="relative pl-9 pb-12 scroll-mt-6">
      <Node status={status} pending={r.pending} selected={selected} />

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="label !text-ink num">Run {String(index).padStart(2, '0')}</span>
        <Mark state={r.pending ? 'awaiting_approval' : status} />
        {running && r.created_at && <Elapsed from={r.created_at} />}
        {r.created_at && !running && <span className="label !text-faint num">
          {new Date(r.created_at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>}
      </div>

      <h2 id={`q-${turn.id}`}
        className={`mt-2 mb-4 text-[20px] font-medium leading-[1.35] tracking-[-0.012em] text-ink whitespace-pre-wrap break-words ${
          longGoal && clamped ? 'line-clamp-4' : ''}`}>
        {r.goal}
      </h2>
      {longGoal && (
        <button type="button" onClick={() => setClamped(!clamped)} className="btn-text -mt-2 mb-4 block">
          {clamped ? 'Show the full request' : 'Show less'}
        </button>
      )}

      {selected && detail?.pending && <Approval runId={detail.id} pending={detail.pending} onDecided={onDecided} />}
      {selected && detail && <Plan run={detail} live={live} />}

      {selected && detail?.verification && !detail.verification.passed && (
        <div role="note" className="my-4 border-l-[3px] border-alert pl-4">
          <div className="label !text-alert inline-flex items-center gap-1.5"><Glyph state="failed" /> Verification failed</div>
          {detail.verification.problems?.map((p, i) => <p key={i} className="text-[14px] text-ink">{p}</p>)}
        </div>
      )}

      {r.answer
        ? <Answer text={r.answer} justFinished={selected && justFinished} onNote={onNote} />
        : running
          ? <>
              <p className="text-[11.5px] font-mono text-signalink flex items-center gap-2.5">
                <Led state="running" />
                {draft
                  ? `${draft.actor || 'Writer'} is writing${draft.final ? '' : ` · ${live?.description || 'a draft'}`}…`
                  : live?.description || (live?.stage ? `${live.stage}…` : 'Starting…')}
              </p>
              {draft?.text && (
                <div className={`mt-3 ${draft.final ? '' : 'max-h-[220px] overflow-hidden opacity-70 [mask-image:linear-gradient(to_bottom,black_70%,transparent)]'}`}
                  aria-live="off">
                  <Markdown text={draft.text} />
                  <span aria-hidden="true" className="inline-block w-[7px] h-[14px] bg-signal align-[-2px] animate-blink" />
                </div>
              )}
            </>
          : !r.pending && <p className="text-[14px] text-muted">No answer recorded.</p>}

      <footer className="mt-5 pt-2 border-t border-rule flex flex-wrap items-center gap-x-5 gap-y-2">
        {r.stats?.latency_ms != null && (
          <span className="num text-[9.5px] text-muted">
            {fmt(r.stats.latency_ms)} · {plural(r.stats.llm_calls, 'model call')} · {plural(r.stats.tool_calls, 'tool')}
            {r.stats.approvals > 0 && ` · ${r.stats.approvals} signed`}
          </span>
        )}
        <button type="button" onClick={() => onSelect(turn.id)} aria-expanded={selected} className="btn-text">
          {selected ? '− Hide working' : '+ Show working'}
        </button>
        {r.answer && (
          <button type="button" className="btn-text"
            onClick={() => { navigator.clipboard?.writeText(r.answer); setCopied(true); setTimeout(() => setCopied(false), 1400) }}>
            {copied ? 'Copied' : 'Copy'}
          </button>
        )}
      </footer>

      {/* Below xl the margin is hidden, so the working is available inline instead. */}
      {selected && detail && (
        <details className="xl:hidden mt-4 border-t border-rule pt-3">
          <summary className="btn-text cursor-pointer select-none">Pipeline &amp; trace</summary>
          <div className="mt-4 grid sm:grid-cols-[210px_1fr] gap-6">
            <Axon run={detail} spans={detail.spans || []} live={live} compact />
            <Trace spans={detail.spans || []} />
          </div>
        </details>
      )}
    </article>
  )
}

/* ── empty state ──────────────────────────────────────────────────────────── */

const SUGGESTIONS = [
  ['research', 'Compare LangGraph, CrewAI and AutoGen, then save the comparison to Research/'],
  ['plan', 'Build a study roadmap for AI engineering and save it to my vault'],
  ['recall', 'What do my notes say about vector databases?'],
  ['schedule', 'Every weekday at 08:30, brief me on my open tasks and the weather'],
]

function Readout({ k, children, state }) {
  return (
    <div className="grid grid-cols-[5.5rem_minmax(0,1fr)] items-center gap-x-3 py-[7px] border-b border-rule last:border-0">
      <dt className="label">{k}</dt>
      <dd className="flex items-center gap-2 min-w-0 font-mono text-[11px] text-ink">
        {state && <Led state={state} />}<span className="truncate">{children}</span>
      </dd>
    </div>
  )
}

export function EmptyJournal({ onPick, health }) {
  const servers = Object.entries(health?.servers || {})
  const up = servers.filter(([, v]) => v === 'up').length
  const ok = health?.ok
  return (
    <div className="min-h-full grid place-items-center py-8">
      <div className="w-full max-w-[720px]">
        <div className="flex items-center gap-2.5">
          <Led state={ok ? 'ok' : health ? 'failed' : 'idle'} />
          <span className="label !text-ink">{ok ? 'System ready' : health ? 'API offline' : 'Connecting'}</span>
          <span className="h-px flex-1 bg-rule" aria-hidden="true" />
          <span className="label num">{new Date().toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' })}</span>
        </div>

        <h1 className="display text-[40px] sm:text-[54px] leading-[1] mt-8">
          Give it a goal.<br /><span className="text-faint">Agents do the work.</span>
        </h1>
        <p className="mt-5 text-[15.5px] leading-relaxed text-ink2 max-w-[56ch]">
          Synapse plans the goal, routes each step to a specialist agent, calls tools through MCP, stops for your
          signature before anything risky, checks its own work, and files what matters in your vault.
        </p>

        <div className="mt-10 grid md:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)] gap-6">
          <section aria-labelledby="try-h" className="panel ticks">
            <h2 id="try-h" className="label !text-ink px-4 py-2 border-b border-rule">Try a goal</h2>
            <ul>
              {SUGGESTIONS.map(([k, s]) => (
                <li key={s} className="border-b border-rule last:border-0">
                  <button type="button" onClick={() => onPick(s)}
                    className="group w-full text-left px-4 py-3 grid grid-cols-[4.5rem_minmax(0,1fr)] gap-x-3 items-baseline hover:bg-paper3/40 transition-colors">
                    <span className="label group-hover:!text-signalink transition-colors">{k}</span>
                    <span className="text-[14px] leading-snug text-ink2 group-hover:text-ink transition-colors">{s}</span>
                  </button>
                </li>
              ))}
            </ul>
          </section>

          <section aria-labelledby="sys-h" className="panel">
            <h2 id="sys-h" className="label !text-ink px-4 py-2 border-b border-rule">Systems</h2>
            <dl className="px-4 py-1">
              <Readout k="model" state={ok ? 'ok' : 'idle'}>{ok ? health.model : '—'}</Readout>
              <Readout k="engine">{ok ? (health.fast ? 'fast + local' : 'local') : '—'}</Readout>
              <Readout k="agents">{ROSTER.length} on standby</Readout>
              <Readout k="mcp" state={!ok ? 'idle' : up === servers.length ? 'ok' : 'failed'}>
                {ok ? `${up}/${servers.length} servers up` : '—'}</Readout>
              <Readout k="tools">{ok ? `${health.tools} behind MCP` : '—'}</Readout>
              <Readout k="vault">{ok ? `${health.notes ?? 0} notes` : '—'}</Readout>
              <Readout k="crew">{ok ? (health.crew ? 'enabled' : 'off') : '—'}</Readout>
            </dl>
          </section>
        </div>
      </div>
    </div>
  )
}
