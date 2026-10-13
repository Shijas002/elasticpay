import { useState } from 'react'
import { Play, Sparkles, TrendingUp, TrendingDown, Terminal, Film } from 'lucide-react'

const TITLES = [
  ['Neon Harbor', 'from-fuchsia-600 to-indigo-700'],
  ['The Last Compiler', 'from-emerald-500 to-teal-800'],
  ['Orbit 9', 'from-sky-500 to-blue-900'],
  ['Midnight Ledger', 'from-amber-500 to-red-800'],
  ['Paper Moons', 'from-rose-500 to-purple-800'],
  ['Cache Me If You Can', 'from-lime-500 to-green-800'],
]

export function StreamFlixApp() {
  const [playing, setPlaying] = useState(null)
  return (
    <div className="space-y-4">
      {playing ? (
        <div className={`pop rounded-xl bg-gradient-to-br ${playing[1]} p-6 aspect-video grid place-items-center relative overflow-hidden`}>
          <div className="text-center">
            <Film className="mx-auto mb-2" size={40} />
            <div className="text-2xl font-bold">{playing[0]}</div>
            <div className="text-sm text-white/70">Now playing - S1 E1 - 4K HDR</div>
          </div>
          <div className="absolute bottom-0 left-0 h-1.5 bg-white/80 w-1/3 animate-pulse" />
          <button className="btn btn-ghost absolute top-3 right-3" onClick={() => setPlaying(null)}>Back</button>
        </div>
      ) : (
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
          {TITLES.map((t) => (
            <button key={t[0]} onClick={() => setPlaying(t)}
              className={`group rounded-xl bg-gradient-to-br ${t[1]} aspect-[16/10] p-3 text-left flex flex-col justify-end hover:scale-[1.02] transition`}>
              <Play size={18} className="opacity-0 group-hover:opacity-100 transition" />
              <span className="font-semibold">{t[0]}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

const SNIPPET = `def settle(order):\n    # TODO: handle partial captures\n    return capture(order.id)\n`

export function CloudDevApp() {
  const [code, setCode] = useState(SNIPPET)
  const [out, setOut] = useState('')
  const assist = () =>
    setOut('AI Assist: add idempotency + retry with exponential backoff.\n\n' +
      'def settle(order, retries=3):\n    for i in range(retries):\n        try:\n            return capture(order.id, request_id=order.id)\n' +
      '        except Timeout:\n            sleep(2 ** i)\n    raise SettlementError(order.id)\n')
  return (
    <div className="space-y-3">
      <div className="rounded-xl bg-black/50 border border-white/10 overflow-hidden">
        <div className="flex items-center gap-2 px-3 py-2 bg-white/5 text-xs text-slate-400"><Terminal size={14} /> settle.py - CloudDev Pro workspace (8 vCPU, GPU)</div>
        <textarea value={code} onChange={(e) => setCode(e.target.value)} spellCheck={false}
          className="w-full h-40 bg-transparent p-3 font-mono text-sm text-emerald-300 outline-none resize-none" />
      </div>
      <button className="btn btn-primary" onClick={assist}><Sparkles size={16} /> Run AI Assist</button>
      {out && <pre className="pop rounded-xl bg-black/50 border border-indigo-400/30 p-3 text-xs text-indigo-200 whitespace-pre-wrap">{out}</pre>}
    </div>
  )
}

const TICKERS = [
  ['NVDA', 912.4, 2.1], ['AAPL', 231.7, -0.4], ['PYPL', 78.9, 1.3], ['TSLA', 244.2, -1.8], ['MSFT', 468.1, 0.7],
]
const HEADLINES = [
  'Fed signals patient stance as inflation cools to 2.4%',
  'PayPal expands agentic checkout pilots across 40 markets',
  'Chip stocks rally on record AI datacenter orders',
  'Subscription fatigue drives 31% of users to usage-based billing, survey finds',
]

function Spark({ up }) {
  const pts = Array.from({ length: 14 }, (_, i) => 12 + Math.sin(i * (up ? 0.7 : 1.1)) * 6 + (up ? -i * 0.6 : i * 0.6))
  return (
    <svg viewBox="0 0 70 30" className="w-20 h-8">
      <polyline fill="none" stroke={up ? '#34d399' : '#f87171'} strokeWidth="2" points={pts.map((y, i) => `${i * 5.4},${y + 6}`).join(' ')} />
    </svg>
  )
}

export function AlphaReadApp() {
  return (
    <div className="grid md:grid-cols-2 gap-4">
      <div className="space-y-2">
        {TICKERS.map(([s, p, c]) => (
          <div key={s} className="flex items-center justify-between rounded-lg bg-white/5 px-3 py-2">
            <span className="font-mono font-semibold w-14">{s}</span>
            <Spark up={c > 0} />
            <span className="font-mono">${p.toFixed(2)}</span>
            <span className={`flex items-center gap-1 text-sm ${c > 0 ? 'text-emerald-400' : 'text-red-400'}`}>
              {c > 0 ? <TrendingUp size={14} /> : <TrendingDown size={14} />} {c > 0 ? '+' : ''}{c}%
            </span>
          </div>
        ))}
      </div>
      <div className="space-y-2">
        {HEADLINES.map((h) => (
          <div key={h} className="rounded-lg bg-white/5 px-3 py-3 text-sm">
            <div className="text-[10px] uppercase tracking-wider text-amber-400 mb-1">Premium analysis</div>{h}
          </div>
        ))}
      </div>
    </div>
  )
}

export const APPS = {
  streamflix: { Component: StreamFlixApp, accent: 'from-fuchsia-500 to-indigo-600', emoji: '🎬' },
  clouddev: { Component: CloudDevApp, accent: 'from-emerald-500 to-teal-600', emoji: '☁️' },
  alpharead: { Component: AlphaReadApp, accent: 'from-amber-500 to-orange-600', emoji: '📈' },
}
