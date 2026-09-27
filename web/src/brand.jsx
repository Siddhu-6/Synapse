/** The mark: two terminals and the gap between them. Filled = the sender, hollow = the receiver,
 *  amber = the signal crossing. The wordmark is Archivo at full width, lowercase. */
export function Logo({ size = 22, live = false }) {
  return (
    <svg width={size} height={size * 10 / 22} viewBox="0 0 22 10" aria-hidden="true" className="shrink-0">
      <rect x="0" y="1" width="8" height="8" fill="rgb(var(--ink))" />
      <rect x="14.75" y="1.75" width="6.5" height="6.5" fill="none" stroke="rgb(var(--ink))" strokeWidth="1.5" />
      <rect x="9.5" y="4" width="3.5" height="2" fill="rgb(var(--signal))" className={live ? 'animate-blink' : ''} />
    </svg>
  )
}

export function Wordmark({ size = 20, live = false }) {
  return (
    <span className="inline-flex items-center gap-2.5 text-ink" style={{ fontSize: size }}>
      <Logo size={size * 1.05} live={live} />
      <span className="lowercase leading-none" style={{ fontStretch: '125%', fontWeight: 700, letterSpacing: '-0.035em' }}>synapse</span>
    </span>
  )
}
