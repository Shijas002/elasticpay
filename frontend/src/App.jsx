import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Zap, Clock, FastForward, RotateCcw, ShieldAlert, Lock, Unlock, X, Check, Loader2, Store, LayoutDashboard,
  Bot, Moon, Flame, PiggyBank, ExternalLink, Cpu, Banknote,
} from 'lucide-react'
import { api, negotiate } from './api'
import { APPS } from './MiniApps'
import CommandCenter, { StatusBadge } from './CommandCenter'

const AGENTS = {
  CONTEXT: { name: 'Context Agent', cls: 'border-sky-400/40 bg-sky-500/10 text-sky-200' },
  MERCHANT: { name: 'Merchant Yield Agent', cls: 'border-amber-400/40 bg-amber-500/10 text-amber-200' },
  CFO: { name: 'Buyer CFO Agent', cls: 'border-emerald-400/40 bg-emerald-500/10 text-emerald-200' },
  PAYPAL: { name: 'PayPal', cls: 'border-blue-400/40 bg-blue-500/10 text-blue-200' },
  WEBHOOK: { name: 'Webhook', cls: 'border-fuchsia-400/40 bg-fuchsia-500/10 text-fuchsia-200' },
  SYSTEM: { name: 'System', cls: 'border-slate-400/30 bg-slate-500/10 text-slate-200' },
}
const TIER_NAME = { DAY: '24-hour Micro-Pass', WEEKEND: '72-hour Weekend Sprint', MONTHLY: 'Full Monthly Pass' }
const fmtDate = (iso) => (iso ? new Date(iso).toUTCString().slice(5, 22) : '')
const fmtLeft = (h) => (h >= 48 ? `${Math.floor(h / 24)}d ${Math.round(h % 24)}h` : `${Math.floor(h)}h ${Math.round((h % 1) * 60)}m`)

/* ------------------------------------------------------------------ Judge Scenario Simulator */
function SimulatorBar({ state, busy, onScenario, onTime, onReset }) {
  const sc = state.scenarios.find((s) => s.key === state.sim.scenario)
  const btn = (key, Icon, label, sub) => (
    <button key={key} disabled={busy} onClick={() => onScenario(key)}
      className={`btn flex-col !items-start !gap-0 text-left ${state.sim.scenario === key ? 'bg-indigo-500 text-white' : 'btn-ghost'}`}>
      <span className="flex items-center gap-2"><Icon size={15} />{label}</span>
      <span className="text-[11px] font-normal opacity-80">{sub}</span>
    </button>
  )
  return (
    <div className="card p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="text-xs font-semibold uppercase tracking-wider text-slate-400">Judge scenario simulator</div>
        <div className="flex items-center gap-2 text-xs text-slate-300">
          <Clock size={14} /> Simulated clock <b>{fmtDate(state.sim.now)} UTC</b> - cycle day <b>{state.sim.cycle_day}/30</b>
        </div>
      </div>
      <div className="flex flex-wrap gap-2">
        {btn('A', Moon, 'A - Late Tuesday Night', '45 mins free - casual intent')}
        {btn('B', Flame, 'B - Weekend Binge / Sprint', 'heavy intent')}
        {btn('C', PiggyBank, 'C - Tight Budget Alert', '$3.00 left in monthly budget')}
        <div className="mx-1 hidden w-px bg-white/10 md:block" />
        <button disabled={busy} className="btn btn-ghost" onClick={() => onTime(24, 0)}><FastForward size={15} /> +24 Hours</button>
        <button disabled={busy} className="btn btn-ghost" onClick={() => onTime(0, 30)}><FastForward size={15} /> +30 Days (Cycle Reset)</button>
        <button disabled={busy} className="btn btn-ghost ml-auto" onClick={onReset}><RotateCcw size={15} /> Reset demo</button>
      </div>
      {sc && <div className="mt-3 text-xs text-slate-400">Active context: <span className="text-slate-200">{sc.label}</span> - {sc.weekday} {sc.clock}, calendar: {sc.calendar}</div>}
    </div>
  )
}

/* ------------------------------------------------------------------ <ElasticPayGate /> (HTTP 402 interceptor) */
function ElasticPayGate({ service, onLaunch, children }) {
  const app = APPS[service.slug]
  if (service.status === 'DORMANT') {
    return (
      <div className="card relative overflow-hidden p-8 text-center">
        <div className={`absolute inset-0 bg-gradient-to-br ${app.accent} opacity-10`} />
        <div className="relative mx-auto max-w-md space-y-4">
          <div className="text-5xl">{app.emoji}</div>
          <div className="inline-flex items-center gap-2 rounded-full border border-amber-400/40 bg-amber-500/10 px-3 py-1 font-mono text-xs text-amber-200">
            <Lock size={12} /> HTTP 402 Payment Required - entitlement DORMANT
          </div>
          <h2 className="text-2xl font-bold">{service.name}</h2>
          <p className="text-sm text-slate-400">
            You are charged <b className="text-slate-100">$0.00</b> while idle. Launch to let your agents negotiate access: 24h from ${service.day_pass_price.toFixed(2)},
            capped at ${service.monthly_cap_price.toFixed(2)} for the whole month.
          </p>
          <button className="btn btn-primary mx-auto !px-5 !py-3 text-base" onClick={() => onLaunch(service)}><Zap size={18} /> Launch App</button>
          <div className="text-xs text-slate-500">{service.dormant_days_saved} dormant days saved so far</div>
        </div>
      </div>
    )
  }
  const unlimited = service.status === 'CAP_UNLOCKED'
  return (
    <div className="space-y-3">
      <div className={`flex flex-wrap items-center justify-between gap-3 rounded-xl border px-4 py-2.5 text-sm ${unlimited ? 'border-emerald-400/30 bg-emerald-500/10' : 'border-indigo-400/30 bg-indigo-500/10'}`}>
        <div className="flex items-center gap-3"><Unlock size={16} /><StatusBadge status={service.status} />
          <span className="text-slate-300">{unlimited ? 'UNLIMITED_CYCLE until' : 'Access expires in'} <b>{fmtLeft(service.hours_remaining)}</b>
            {unlimited ? ` (${fmtDate(service.access_expires_at)})` : ''} - paid ${service.current_cycle_spend.toFixed(2)} of ${service.monthly_cap_price.toFixed(2)}</span>
        </div>
        {!unlimited && (
          <button className="btn btn-ghost" onClick={() => onLaunch(service, 'MONTHLY')}>
            <Banknote size={15} /> Go unlimited - pay only ${service.cap_remaining.toFixed(2)} more
          </button>
        )}
      </div>
      <div className="card p-4">{children}</div>
    </div>
  )
}

/* ------------------------------------------------------------------ Negotiation modal */
function NegotiationModal({ m, onClose, onApprove, onDecline }) {
  const endRef = useRef(null)
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }) }, [m?.events.length, m?.phase])
  if (!m) return null
  const finished = ['done', 'declined', 'error', 'cancelled'].includes(m.phase)
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/70 p-4 backdrop-blur-sm">
      <div className="card flex max-h-[90vh] w-full max-w-2xl flex-col overflow-hidden border-white/20 bg-ink-900">
        <div className="flex items-center justify-between border-b border-white/10 px-5 py-3">
          <div className="flex items-center gap-2 font-semibold"><Bot size={18} className="text-indigo-300" /> Agent negotiation - {m.service.name}</div>
          <button onClick={onClose} className="rounded p-1 hover:bg-white/10" aria-label="Close"><X size={18} /></button>
        </div>
        <div className="flex-1 space-y-2 overflow-auto px-5 py-4">
          {m.events.map((e, i) => {
            const a = AGENTS[e.agent] || AGENTS.SYSTEM
            return (
              <div key={i} className={`pop rounded-xl border px-3 py-2 text-sm ${a.cls}`}>
                <div className="mb-0.5 text-[10px] font-bold uppercase tracking-wider opacity-80">{a.name}</div>
                <div className="break-words text-slate-100/90">{e.text}</div>
              </div>
            )
          })}
          {m.phase === 'running' && <div className="flex items-center gap-2 text-sm text-slate-400"><Loader2 className="animate-spin" size={16} /> Agents are reasoning...</div>}
          <div ref={endRef} />
        </div>
        <div className="border-t border-white/10 px-5 py-4">
          {m.phase === 'approval' && (
            <div className="space-y-3">
              <div className="flex items-start gap-2 rounded-lg border border-amber-400/40 bg-amber-500/10 p-3 text-sm text-amber-100">
                <ShieldAlert size={18} className="mt-0.5 shrink-0" />
                <span>Your CFO agent wants to spend <b>${m.proposal.charge.toFixed(2)}</b> on the {TIER_NAME[m.proposal.pass_type]}
                  {m.proposal.proration_credit > 0 && <> (after a <b>${m.proposal.proration_credit.toFixed(2)}</b> Rent-to-Own credit)</>}.
                  That is above your auto-approve limit, so it needs your OK.</span>
              </div>
              <div className="flex gap-2">
                <button className="btn btn-primary" onClick={onApprove}><Check size={16} /> Approve ${m.proposal.charge.toFixed(2)}</button>
                <button className="btn btn-ghost" onClick={onDecline}>Decline</button>
              </div>
            </div>
          )}
          {m.phase === 'paypal' && (
            <div className="space-y-2">
              <div className="text-sm text-blue-200">PayPal sandbox order <span className="font-mono">{m.order_id}</span> created. Approve it in the PayPal popup with a sandbox buyer account.</div>
              <div className="flex items-center gap-2">
                <button className="btn btn-primary" onClick={() => window.open(m.approve_url, 'paypal', 'width=480,height=720')}><ExternalLink size={16} /> Open PayPal approval</button>
                <span className="flex items-center gap-2 text-xs text-slate-400"><Loader2 className="animate-spin" size={14} /> waiting for capture...</span>
              </div>
            </div>
          )}
          {m.phase === 'done' && (
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-2 text-emerald-300"><Unlock size={18} /> Unlocked - charged ${m.result.ledger.final_charged_amount.toFixed(2)}
                {m.result.ledger.proration_credit_applied > 0 && <span className="text-xs text-slate-400">(proration credit ${m.result.ledger.proration_credit_applied.toFixed(2)})</span>}</div>
              <button className="btn btn-primary" onClick={onClose}>Open {m.service.name}</button>
            </div>
          )}
          {m.phase === 'declined' && (
            <div className="flex items-center justify-between gap-3"><div className="text-slate-300">No charge made. {m.service.name} stays DORMANT at $0.00.</div><button className="btn btn-ghost" onClick={onClose}>Close</button></div>
          )}
          {m.phase === 'cancelled' && (
            <div className="flex items-center justify-between gap-3"><div className="text-slate-300">Cancelled - nothing was charged.</div><button className="btn btn-ghost" onClick={onClose}>Close</button></div>
          )}
          {m.phase === 'error' && (
            <div className="flex items-center justify-between gap-3"><div className="text-red-300">{m.error}</div><button className="btn btn-ghost" onClick={onClose}>Close</button></div>
          )}
          {!finished && m.phase === 'running' && <div className="text-xs text-slate-500">Streaming live from POST /api/negotiate-and-unlock</div>}
        </div>
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ App */
export default function App() {
  const [state, setState] = useState(null)
  const [ledger, setLedger] = useState([])
  const [telemetry, setTelemetry] = useState([])
  const [view, setView] = useState('merchants')
  const [active, setActive] = useState('streamflix')
  const [modal, setModal] = useState(null)
  const [busy, setBusy] = useState(false)
  const [toast, setToast] = useState('')
  const pollRef = useRef(null)

  const refresh = useCallback(async () => {
    const [s, l] = await Promise.all([api.state(), api.ledger()])
    setState(s)
    setLedger(l)
  }, [])
  useEffect(() => { refresh().catch((e) => setToast(`Backend unreachable: ${e.message}`)) }, [refresh])
  useEffect(() => { if (!toast) return undefined; const t = setTimeout(() => setToast(''), 5000); return () => clearTimeout(t) }, [toast])

  const stopPolling = () => { clearInterval(pollRef.current); pollRef.current = null }
  useEffect(() => () => stopPolling(), [])

  // PayPal popup finished (postMessage from /api/paypal/return) -> refresh and close the flow
  useEffect(() => {
    const onMsg = async (e) => {
      if (e.data?.type === 'elasticpay:paid') {
        stopPolling()
        await refresh()
        setModal((m) => m && { ...m, phase: 'done', result: { ledger: { final_charged_amount: m.charge || 0, proration_credit_applied: m.credit || 0 } } })
      } else if (e.data?.type === 'elasticpay:cancelled') {
        stopPolling(); await refresh(); setModal((m) => m && { ...m, phase: 'cancelled' })
      }
    }
    window.addEventListener('message', onMsg)
    return () => window.removeEventListener('message', onMsg)
  }, [refresh])

  const run = useCallback(async (service, body, keepEvents) => {
    setModal((m) => ({ service, events: keepEvents && m ? m.events : [], phase: 'running' }))
    const onEvent = (ev) => {
      if (ev.type === 'trace') {
        setTelemetry((t) => [...t, ev])
        setModal((m) => m && { ...m, events: [...m.events, ev] })
      } else if (ev.type === 'approval_required') {
        setModal((m) => m && { ...m, phase: 'approval', proposal: ev.outcome, proposal_id: ev.proposal_id })
      } else if (ev.type === 'paypal_approval') {
        setModal((m) => m && { ...m, phase: 'paypal', order_id: ev.order_id, approve_url: ev.approve_url, charge: ev.charge, credit: ev.credit })
        window.open(ev.approve_url, 'paypal', 'width=480,height=720')
        stopPolling()
        pollRef.current = setInterval(async () => {
          try {
            const s = await api.orderStatus(ev.order_id)
            if (s.status === 'COMPLETED') { stopPolling(); await refresh(); setModal((m) => m && { ...m, phase: 'done', result: { ledger: { final_charged_amount: m.charge || 0, proration_credit_applied: m.credit || 0 } } }) }
            if (s.status === 'CANCELLED' || s.status === 'FAILED') { stopPolling(); await refresh(); setModal((m) => m && { ...m, phase: 'cancelled' }) }
          } catch { /* keep polling */ }
        }, 2000)
      } else if (ev.type === 'result') {
        setState(ev.state)
        api.ledger().then(setLedger)
        setModal((m) => m && { ...m, phase: ev.status === 'COMPLETED' ? 'done' : 'declined', result: ev })
      } else if (ev.type === 'error') {
        setModal((m) => m && { ...m, phase: 'error', error: ev.message })
      }
    }
    try { await negotiate(body, onEvent) } catch (e) { setModal((m) => m && { ...m, phase: 'error', error: e.message }) }
  }, [refresh])

  const launch = (service, forceTier) => run(service, { service_id: service.id, force_tier: forceTier || null })
  const closeModal = async () => { stopPolling(); setModal(null); await refresh() }

  const guard = (fn) => async (...a) => {
    setBusy(true)
    try { await fn(...a) } catch (e) { setToast(e.message) } finally { setBusy(false) }
  }
  const onScenario = guard(async (key) => setState(await api.scenario(key)))
  const onTime = guard(async (h, d) => {
    const r = await api.time(h, d)
    setState(r.state)
    setLedger(await api.ledger())
    setToast(r.cycle_reset ? `Cycle reset. ${r.notes[0] || ''}` : `+${h ? `${h} hours` : `${d} days`} - expired passes fall back to DORMANT at $0.00`)
  })
  const onReset = guard(async () => { setState(await api.reset()); setLedger([]); setTelemetry([]); setToast('Demo reset to the 20th with 19 dormant days saved') })

  if (!state) return <div className="grid h-screen place-items-center text-slate-400"><Loader2 className="animate-spin" /> {toast || 'Connecting to ElasticPay...'}</div>
  const svc = state.services.find((s) => s.slug === active)
  const App = APPS[active].Component

  return (
    <div className="mx-auto max-w-7xl space-y-5 px-4 py-5">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="grid h-10 w-10 place-items-center rounded-xl bg-gradient-to-br from-indigo-500 to-fuchsia-500"><Zap size={22} /></div>
          <div>
            <h1 className="text-xl font-bold leading-none">ElasticPay</h1>
            <div className="text-xs text-slate-400">Agent-negotiated JIT subscriptions - $0.00 until you actually use it</div>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className={`rounded-full border px-2.5 py-1 ${state.paypal.mode === 'PAYPAL_SANDBOX' ? 'border-blue-400/40 bg-blue-500/15 text-blue-200' : 'border-slate-400/30 bg-slate-500/15 text-slate-300'}`}>
            PayPal: {state.paypal.mode === 'PAYPAL_SANDBOX' ? 'Sandbox (live API)' : 'Simulated sandbox'}
          </span>
          <span className="flex items-center gap-1 rounded-full border border-emerald-400/30 bg-emerald-500/10 px-2.5 py-1 text-emerald-200">
            <Cpu size={12} /> Agents: {state.llm.enabled ? `${state.llm.provider}/${state.llm.model}` : 'deterministic engine'}
          </span>
          <span className="rounded-full border border-white/10 bg-white/5 px-2.5 py-1">Budget left ${state.wallet.budget_left.toFixed(2)}</span>
        </div>
      </header>

      <SimulatorBar state={state} busy={busy} onScenario={onScenario} onTime={onTime} onReset={onReset} />

      <nav className="flex gap-2">
        {[['merchants', Store, 'Merchant Ecosystem'], ['command', LayoutDashboard, 'Financial Command Center']].map(([k, Icon, label]) => (
          <button key={k} onClick={() => setView(k)} className={`btn ${view === k ? 'bg-white text-ink-900' : 'btn-ghost'}`}><Icon size={16} />{label}</button>
        ))}
      </nav>

      {view === 'merchants' ? (
        <div className="space-y-4">
          <div className="flex flex-wrap gap-2">
            {state.services.map((s) => (
              <button key={s.slug} onClick={() => setActive(s.slug)}
                className={`card flex items-center gap-3 px-4 py-2.5 text-left transition ${active === s.slug ? 'ring-2 ring-indigo-400' : 'opacity-80 hover:opacity-100'}`}>
                <span className="text-xl">{APPS[s.slug].emoji}</span>
                <span>
                  <div className="text-sm font-semibold">{s.name}</div>
                  <div className="text-[11px] text-slate-400">${s.day_pass_price.toFixed(2)}/24h - cap ${s.monthly_cap_price.toFixed(2)}</div>
                </span>
                <StatusBadge status={s.status} />
              </button>
            ))}
          </div>
          <ElasticPayGate service={svc} onLaunch={launch}><App /></ElasticPayGate>
        </div>
      ) : (
        <CommandCenter state={state} ledger={ledger} telemetry={telemetry} />
      )}

      <NegotiationModal m={modal} onClose={closeModal}
        onApprove={() => run(modal.service, { proposal_id: modal.proposal_id }, true)}
        onDecline={() => { setModal((m) => ({ ...m, phase: 'cancelled' })) }} />
      {toast && <div className="fixed bottom-4 left-1/2 z-50 -translate-x-1/2 rounded-lg border border-white/10 bg-ink-700 px-4 py-2 text-sm shadow-xl">{toast}</div>}
    </div>
  )
}
