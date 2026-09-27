/** The margin: what Synapse is doing, as an instrument readout beside the journal.
 *
 *  Top — the pipeline: seven graph stages as LEDs on one wire. The wire is inked up to the furthest
 *  stage reached, and while a stage is firing an amber impulse travels down to it.
 *  Bottom — the trace: every span, in order, with its tool, latency and any error.               */
import { Led, Mark, STAGES, fmt, stageStates } from './panels'

export { STAGES, stageStates }

export function Axon({ run, spans, live, compact = false }) {
  const states = stageStates(run, spans, live)
  const reached = states.reduce((m, s, i) => (s !== 'idle' ? i : m), -1)
  const firing = states.findIndex((s) => s === 'running' || s === 'waiting')
  const ROW = compact ? 32 : 40
  const CY = 7                                          // y of an LED's centre within its row

  return (
    <div className="relative" style={{ width: compact ? 200 : '100%', height: ROW * (STAGES.length - 1) + 18 }}>
      <div className="absolute left-[3px] w-px bg-rule2" style={{ top: CY, height: ROW * (STAGES.length - 1) }} />
      {reached > 0 && <div className="absolute left-[3px] w-px bg-ink2 transition-[height] duration-500"
        style={{ top: CY, height: reached * ROW }} />}
      {firing > 0 && states[firing] === 'running' && (
        <div className="absolute left-[1px] w-[5px] overflow-hidden" style={{ top: CY, height: firing * ROW }}>
          <div className="absolute left-0 w-full h-7 animate-impulse"
            style={{ background: 'linear-gradient(to bottom, transparent, rgb(var(--signal)) 60%, transparent)',
                     boxShadow: '0 0 10px rgb(var(--signal) / .6)' }} />
        </div>
      )}

      {STAGES.map(([name, sub], i) => {
        const st = states[i]
        const n = spans.filter((s) => s.name === name).length
        return (
          <div key={name} className="absolute left-0 right-0 flex items-start gap-3" style={{ top: i * ROW }}>
            <Led state={st} className="mt-[3.5px] bg-paper2" />
            <div className="flex-1 flex items-baseline justify-between gap-2 leading-tight">
              <div>
                <div className={`font-mono text-[11px] ${st === 'running' ? 'text-signalink' : st === 'idle' ? 'text-faint' : 'text-ink'}`}>
                  {name}{n > 1 && <span className="text-[9.5px] text-muted ml-1">×{n}</span>}
                </div>
                {!compact && <div className="text-[11.5px] text-muted">{sub}</div>}
              </div>
              {!compact && st !== 'idle' && <Mark state={st} />}
            </div>
          </div>
        )
      })}
    </div>
  )
}

export function Trace({ spans }) {
  if (!spans.length) return <p className="text-[13px] text-muted">Nothing recorded yet.</p>
  return (
    <ol>
      {spans.map((s, i) => {
        const a = s.attrs || {}
        const st = a.error ? 'failed' : a.status === 'failed' ? 'failed' : a.status === 'blocked' ? 'blocked'
          : a.status === 'rejected' ? 'rejected' : 'ok'
        const tok = (a.llm || []).reduce((n, c) => n + (c.output_tokens || 0), 0)
        return (
          <li key={i} className="py-2 hair fade-in">
            <div className="flex items-baseline gap-2">
              <span className="num text-[9.5px] text-faint w-5">{String(i + 1).padStart(2, '0')}</span>
              <span className="font-mono text-[11px] text-ink">{s.name}</span>
              {a.step && <span className="num text-[9.5px] text-muted">{a.step}</span>}
              <span className="ml-auto num text-[9.5px] text-muted">{fmt(s.latency_ms)}</span>
            </div>
            <div className="pl-7">
              {a.tool && <div className="flex items-center gap-2 mt-0.5"><code className="font-mono text-[10.5px] text-ink2">{a.tool}</code><Mark state={st} /></div>}
              {a.description && <div className="text-[12.5px] text-muted leading-snug">{a.description}</div>}
              {a.recovered && <div className="text-[12px] text-signalink">↳ {a.recovered}</div>}
              {a.arg_repair && <div className="text-[12px] text-signalink">↳ arguments repaired</div>}
              {a.injection_flags && <div className="text-[12px] font-medium text-alert">injection pattern flagged</div>}
              {a.error && <div className="text-[12px] text-ink leading-snug break-words border-l-2 border-alert pl-2 mt-0.5">{a.error}</div>}
              {tok > 0 && <div className="num text-[9.5px] text-faint mt-0.5">{tok} tokens out</div>}
            </div>
          </li>
        )
      })}
    </ol>
  )
}

export function Margin({ run, spans, live }) {
  const running = run?.status === 'running'
  return (
    <div className="h-full flex flex-col min-h-0">
      <section aria-labelledby="pipe-h" className="pb-5 border-b border-rule">
        <div className="flex items-baseline justify-between mb-4">
          <h2 id="pipe-h" className="label !text-ink">Pipeline</h2>
          {running ? <span className="label !text-signalink">live</span> : run && <span className="label num">{run.id?.slice(0, 8)}</span>}
        </div>
        <Axon run={run} spans={spans} live={live} />
        <p className="mt-5 font-mono text-[10.5px] text-muted min-h-[2.6em] leading-relaxed" aria-live="polite">
          {running && live?.stage === 'step'
            ? `step ${live.index}/${live.total} · ${live.actor || live.kind}${live.description ? ` — ${live.description}` : ''}`
            : running && live?.stage === 'crew' ? `${live.agent} finished`
              : running ? `${live?.stage || 'starting'}…`
                : run ? `${run.status === 'success' ? 'finished' : run.status === 'awaiting_approval' ? 'waiting for your signature'
                  : run.status === 'failed' || run.status === 'error' ? 'stopped with an error' : run.status}${
                  run.stats?.latency_ms != null && run.status !== 'awaiting_approval' ? ` in ${fmt(run.stats.latency_ms)}` : ''}`
                  : 'idle'}
        </p>
      </section>

      <section aria-labelledby="trace-h" className="pt-4 flex-1 min-h-0 overflow-auto">
        <h2 id="trace-h" className="label !text-ink mb-1">Trace{spans.length ? <span className="text-muted"> · {spans.length} spans</span> : ''}</h2>
        <Trace spans={spans} />
      </section>
    </div>
  )
}
