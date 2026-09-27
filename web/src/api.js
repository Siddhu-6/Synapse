const token = () => localStorage.getItem('synapse_token') || ''

/** Every request has a deadline, so a stuck request becomes a visible error instead of silence. */
async function req(path, opts = {}) {
  const { timeout = 20000, ...rest } = opts
  const ctl = new AbortController()
  const timer = setTimeout(() => ctl.abort(), timeout)
  let r
  try {
    r = await fetch(`/api${path}`, {
      ...rest,
      signal: ctl.signal,
      headers: { 'Content-Type': 'application/json', ...(token() ? { Authorization: `Bearer ${token()}` } : {}), ...(rest.headers || {}) },
    })
  } catch (e) {
    const err = new Error(e.name === 'AbortError' ? 'timeout' : String(e.message || e))
    err.status = e.name === 'AbortError' ? 408 : 0
    throw err
  } finally {
    clearTimeout(timer)
  }
  if (!r.ok) {
    const err = new Error((await r.text()).slice(0, 300) || r.statusText)
    err.status = r.status
    throw err
  }
  return r.status === 204 ? null : r.json()
}

export const api = {
  health: () => req('/health'),
  metrics: () => req('/metrics'),
  tools: () => req('/tools'),
  runs: ({ limit = 200, conversationId } = {}) =>
    req(`/runs?limit=${limit}${conversationId ? `&conversation_id=${conversationId}` : ''}`),
  conversations: () => req('/conversations'),
  run: (id) => req(`/runs/${id}`),
  start: (goal, conversation_id = '') =>
    req('/runs', { method: 'POST', body: JSON.stringify({ goal, conversation_id }), timeout: 15000 }),
  approve: (id, approved, comment = '') =>
    req(`/runs/${id}/approve`, { method: 'POST', body: JSON.stringify({ approved, comment }) }),
  memory: () => req('/memory'),
  forget: (id) => req(`/memory/${id}`, { method: 'DELETE' }),
  deleteConversation: (id) => req(`/conversations/${id}`, { method: 'DELETE' }),
  vaultGraph: () => req('/vault/graph'),
  note: (path) => req(`/vault/note?path=${encodeURIComponent(path)}`),
  deleteNote: (path) => req(`/vault/note?path=${encodeURIComponent(path)}`, { method: 'DELETE' }),
  evals: () => req('/evals/latest'),
  models: () => req('/models'),
  setModel: (id) => req('/models', { method: 'POST', body: JSON.stringify({ id }) }),

  /** Live updates. A browser allows only ~6 connections per host, and every open dashboard tab used
   *  to hold one forever — with enough tabs open, new requests queued behind them and "Run" did
   *  nothing. So only a visible tab keeps the stream; a hidden tab lets go and catches up
   *  (`onResync`) when it comes back. */
  stream: (runId, onMsg, onResync) => {
    let es = null
    const open = () => {
      if (es) return
      const q = new URLSearchParams({ run_id: runId })
      if (token()) q.set('token', token())
      es = new EventSource(`/api/stream?${q}`)
      es.onmessage = (e) => onMsg(JSON.parse(e.data))
      es.onopen = () => onResync?.()
    }
    const shut = () => { es?.close(); es = null }
    const onVis = () => (document.visibilityState === 'hidden' ? shut() : open())
    document.addEventListener('visibilitychange', onVis)
    window.addEventListener('pagehide', shut)
    if (document.visibilityState !== 'hidden') open()
    return {
      close: () => {
        document.removeEventListener('visibilitychange', onVis)
        window.removeEventListener('pagehide', shut)
        shut()
      },
    }
  },
}

export const setToken = (t) => localStorage.setItem('synapse_token', t)
