const json = async (r) => {
  if (!r.ok) throw new Error((await r.text()) || r.statusText)
  return r.json()
}
const post = (url, body = {}) =>
  fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then(json)

export const api = {
  state: () => fetch('/api/state').then(json),
  ledger: () => fetch('/api/ledger').then(json),
  scenario: (scenario) => post('/api/scenario', { scenario }),
  time: (hours = 0, days = 0) => post('/api/simulate-time', { hours, days }),
  reset: () => post('/api/reset'),
  orderStatus: (id) => fetch(`/api/ledger/status/${id}`).then(json),
}

/** POST /api/negotiate-and-unlock and invoke onEvent for each NDJSON line as it streams in. */
export async function negotiate(body, onEvent) {
  const res = await fetch('/api/negotiate-and-unlock', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error((await res.text()) || res.statusText)
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    let i
    while ((i = buf.indexOf('\n')) >= 0) {
      const lineText = buf.slice(0, i).trim()
      buf = buf.slice(i + 1)
      if (lineText) onEvent(JSON.parse(lineText))
    }
  }
}
