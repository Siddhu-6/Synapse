/** Markdown renderer, typeset for reading rather than for a chat bubble.
 *
 *  Headings, booktabs-style tables (top and bottom rules, no grid), checkbox lists, code, emphasis,
 *  links and [[wikilinks]]. Hand-written instead of an npm dependency: the input is our own model's
 *  output and the surface is small, so ~4KB beats ~60KB. Everything is escaped before any markup is
 *  added, so model output cannot inject HTML.                                                        */
import { useMemo } from 'react'

const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]))
const safeHref = (u) => (/^(https?:|mailto:)/i.test(u) ? u : '#')

function inline(s) {
  let h = esc(s)
  h = h.replace(/`([^`]+)`/g, '<code class="font-mono text-[0.8em] px-1 py-px bg-card border border-rule text-ink">$1</code>')
  h = h.replace(/\*\*([^*]+)\*\*/g, '<strong class="font-semibold text-ink">$1</strong>')
  h = h.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>')
  h = h.replace(/\[\[([^\]]+)\]\]/g,
    '<button type="button" data-note="$1" class="text-ink underline decoration-dotted decoration-signal decoration-[1.5px] underline-offset-[4px] hover:text-signalink">$1</button>')
  h = h.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_, label, url) =>
    `<a href="${safeHref(url)}" target="_blank" rel="noreferrer" class="text-ink underline underline-offset-[3px] decoration-signal/70 hover:decoration-signal hover:text-signalink">${label}</a>`)
  // bare URLs become links too — models often cite sources without markdown link syntax
  h = h.replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g,
    '$1<a href="$2" target="_blank" rel="noreferrer" class="text-ink underline underline-offset-[3px] decoration-signal/70 break-all">$2</a>')
  return h
}

const cell = (row) => row.replace(/^\||\|$/g, '').split('|').map((c) => c.trim())
const box = (done) => `<span aria-hidden="true" class="inline-block w-4 -ml-5 ${done ? 'text-ink' : 'text-faint'}">${done ? '☑' : '☐'}</span>`

export function Markdown({ text, className = '', onNote }) {
  const html = useMemo(() => {
    const lines = (text || '').split('\n')
    const out = []
    let i = 0
    const list = (items, ordered) => {
      if (!items.length) return
      out.push(`<${ordered ? 'ol' : 'ul'} class="my-2.5 pl-5 space-y-1 ${ordered ? 'list-decimal' : 'list-disc'} marker:text-faint">`
        + items.map((x) => `<li class="pl-1">${x}</li>`).join('') + `</${ordered ? 'ol' : 'ul'}>`)
    }
    while (i < lines.length) {
      const line = lines[i]

      if (/^```/.test(line)) {
        const body = []
        i++
        while (i < lines.length && !/^```/.test(lines[i])) body.push(lines[i++])
        i++
        out.push(`<pre class="my-3 px-4 py-3 bg-card border border-rule overflow-auto"><code class="font-mono text-[11.5px] leading-relaxed text-ink">${esc(body.join('\n'))}</code></pre>`)
        continue
      }

      if (/^\|.*\|/.test(line) && /^\|[\s:|-]+\|/.test(lines[i + 1] || '')) {
        const head = cell(line)
        i += 2
        const rows = []
        while (i < lines.length && /^\|.*\|/.test(lines[i])) rows.push(cell(lines[i++]))
        out.push(
          '<div class="my-4 overflow-x-auto"><table class="w-auto min-w-[50%] text-[14px] border-t border-b border-rule2">'
          + '<thead><tr>' + head.map((h) =>
            `<th class="text-left font-mono font-medium text-[9.5px] uppercase tracking-[0.1em] text-muted px-3 pt-2 pb-1.5 border-b border-rule2 whitespace-nowrap">${inline(h)}</th>`).join('')
          + '</tr></thead><tbody>'
          + rows.map((r) => '<tr class="border-b border-rule last:border-0">' + r.map((c) => {
            const m = /^\[( |x)\]\s*/i.exec(c)
            const mark = m ? `<span aria-hidden="true" class="mr-1.5 ${m[1].toLowerCase() === 'x' ? 'text-ink' : 'text-faint'}">${m[1].toLowerCase() === 'x' ? '☑' : '☐'}</span>` : ''
            return `<td class="px-3 py-1.5 align-top text-ink2">${mark}${inline(m ? c.slice(m[0].length) : c)}</td>`
          }).join('') + '</tr>').join('')
          + '</tbody></table></div>')
        continue
      }

      const h = /^(#{1,6})\s+(.*)$/.exec(line)
      if (h) {
        const lvl = Math.min(h[1].length, 4)
        const cls = {
          1: 'display text-[24px] leading-tight mt-6 mb-2',
          2: 'display text-[19px] leading-snug mt-6 mb-1.5',
          3: 'text-[15.5px] mt-5 mb-1 text-ink font-semibold',
          4: 'font-mono font-medium text-[9.5px] uppercase tracking-[0.1em] mt-4 mb-1 text-muted',
        }[lvl]
        out.push(`<h${lvl + 2} class="${cls}">${inline(h[2])}</h${lvl + 2}>`)
        i++
        continue
      }

      if (/^\s*[-*]\s+\[( |x)\]/i.test(line)) {
        const items = []
        while (i < lines.length && /^\s*[-*]\s+\[( |x)\]/i.test(lines[i])) {
          const m = /^\s*[-*]\s+\[( |x)\]\s*(.*)$/i.exec(lines[i++])
          const done = m[1].toLowerCase() === 'x'
          items.push(`<li class="pl-5">${box(done)}<span class="${done ? 'line-through text-muted' : ''}">${inline(m[2])}</span></li>`)
        }
        out.push(`<ul class="my-2.5 space-y-1" role="list">${items.join('')}</ul>`)
        continue
      }

      if (/^\s*[-*]\s+/.test(line)) {
        const items = []
        while (i < lines.length && /^\s*[-*]\s+/.test(lines[i]) && !/^\s*[-*]\s+\[( |x)\]/i.test(lines[i])) {
          items.push(inline(lines[i++].replace(/^\s*[-*]\s+/, '')))
        }
        list(items, false)
        continue
      }

      if (/^\s*\d+[.)]\s+/.test(line)) {
        const items = []
        while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) items.push(inline(lines[i++].replace(/^\s*\d+[.)]\s+/, '')))
        list(items, true)
        continue
      }

      if (/^\s*(---|___|\*\*\*)\s*$/.test(line)) { out.push('<hr class="my-5 border-0 h-px bg-rule" />'); i++; continue }
      if (/^>\s?/.test(line)) {
        const q = []
        while (i < lines.length && /^>\s?/.test(lines[i])) q.push(lines[i++].replace(/^>\s?/, ''))
        out.push(`<blockquote class="my-3 pl-4 border-l-2 border-rule2 text-muted">${inline(q.join(' '))}</blockquote>`)
        continue
      }
      if (!line.trim()) { i++; continue }

      const para = [lines[i++]]              // always consume this line, so a stray "|" can't stall the loop
      while (i < lines.length && lines[i].trim() && !/^(#{1,6}\s|```|\s*[-*]\s|\s*\d+[.)]\s|\||>)/.test(lines[i])) para.push(lines[i++])
      out.push(`<p class="my-2.5">${inline(para.join(' '))}</p>`)
    }
    return out.join('')
  }, [text])

  const click = (e) => {
    const note = e.target?.closest?.('[data-note]')?.dataset?.note
    if (note && onNote) { e.preventDefault(); onNote(note) }
  }
  return (
    <div onClick={click} className={`font-sans text-[15px] leading-[1.7] text-ink2 [&>*:first-child]:mt-0 ${className}`}
      dangerouslySetInnerHTML={{ __html: html }} />
  )
}
