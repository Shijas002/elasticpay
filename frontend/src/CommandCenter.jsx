import { useMemo, useState } from 'react'
import { AgGridReact } from 'ag-grid-react'
import { AllCommunityModule, ModuleRegistry, themeQuartz, colorSchemeDark } from 'ag-grid-community'
import { PiggyBank, CalendarOff, Wallet, Receipt, Bot } from 'lucide-react'

ModuleRegistry.registerModules([AllCommunityModule])

const theme = themeQuartz.withPart(colorSchemeDark).withParams({
  backgroundColor: '#0f1630',
  foregroundColor: '#e2e8f0',
  headerBackgroundColor: '#161f40',
  oddRowBackgroundColor: '#0d142b',
  accentColor: '#6366f1',
  borderColor: 'rgba(255,255,255,0.08)',
  fontSize: 13,
})

export const STATUS_META = {
  DORMANT: { label: 'DORMANT', cls: 'bg-slate-500/20 text-slate-300 border-slate-400/30', dot: 'bg-slate-400' },
  ACTIVE_PASS: { label: 'JIT_ACTIVE', cls: 'bg-indigo-500/20 text-indigo-200 border-indigo-400/40', dot: 'bg-indigo-400 animate-pulse' },
  CAP_UNLOCKED: { label: 'CAP_UNLOCKED', cls: 'bg-emerald-500/20 text-emerald-200 border-emerald-400/40', dot: 'bg-emerald-400' },
}

export function StatusBadge({ status }) {
  const m = STATUS_META[status] || STATUS_META.DORMANT
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-semibold ${m.cls}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${m.dot}`} />{m.label}
    </span>
  )
}

const StatusCell = ({ value }) => <StatusBadge status={value} />

const ProgressCell = ({ data }) => {
  const done = data.progress_pct >= 100
  return (
    <div className="flex h-full flex-col justify-center gap-1" title="Every micro-payment credits toward the monthly cap (Rent-to-Own)">
      <div className="h-2 w-full overflow-hidden rounded-full bg-white/10">
        <div className={`h-full rounded-full transition-all duration-700 ${done ? 'bg-emerald-400' : 'bg-gradient-to-r from-indigo-500 to-fuchsia-500'}`}
          style={{ width: `${Math.max(2, data.progress_pct)}%` }} />
      </div>
      <div className="text-[11px] leading-none text-slate-400">
        ${data.current_cycle_spend.toFixed(2)} / ${data.monthly_cap_price.toFixed(2)} ({data.progress_pct}%)
      </div>
    </div>
  )
}

const fmtHours = (h) => {
  if (!h) return '-'
  const d = Math.floor(h / 24)
  return d ? `${d}d ${Math.round(h - d * 24)}h` : `${Math.floor(h)}h ${Math.round((h % 1) * 60)}m`
}
const money = (p) => (p.value == null ? '' : `$${Number(p.value).toFixed(2)}`)

const TIER_CLS = {
  DAY: 'bg-sky-500/20 text-sky-200', WEEKEND: 'bg-violet-500/20 text-violet-200',
  MONTHLY: 'bg-emerald-500/20 text-emerald-200', NONE: 'bg-slate-500/20 text-slate-300',
}
const TierCell = ({ value }) => (
  <span className={`rounded px-2 py-0.5 text-[11px] font-semibold ${TIER_CLS[value] || ''}`}>
    {{ DAY: '24h Micro-Pass', WEEKEND: '72h Sprint', MONTHLY: 'Monthly Bundle', NONE: 'No purchase' }[value] || value}
  </span>
)
const LedgerStatus = ({ value }) => {
  const cls = { COMPLETED: 'text-emerald-300', PENDING: 'text-amber-300', DECLINED: 'text-slate-400', CANCELLED: 'text-slate-400', FAILED: 'text-red-300' }[value]
  return <span className={`text-xs font-semibold ${cls}`}>{value}</span>
}
const ModeCell = ({ value }) => (
  <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${value === 'PAYPAL_SANDBOX' ? 'bg-blue-500/25 text-blue-200' : 'bg-slate-500/25 text-slate-300'}`}>
    {value === 'PAYPAL_SANDBOX' ? 'PAYPAL SANDBOX' : value === 'NONE' ? '-' : 'SIMULATED'}
  </span>
)

const AGENT_CLS = { CONTEXT: 'text-sky-300', MERCHANT: 'text-amber-300', CFO: 'text-emerald-300', PAYPAL: 'text-blue-300', WEBHOOK: 'text-fuchsia-300', SYSTEM: 'text-slate-300' }

function Kpi({ icon: Icon, label, value, sub, tone }) {
  return (
    <div className="card p-4">
      <div className="flex items-center gap-2 text-xs uppercase tracking-wider text-slate-400"><Icon size={14} className={tone} />{label}</div>
      <div className="mt-1 text-2xl font-bold">{value}</div>
      <div className="text-xs text-slate-400">{sub}</div>
    </div>
  )
}

export default function CommandCenter({ state, ledger, telemetry }) {
  const [selected, setSelected] = useState(null)
  const services = state.services

  const serviceCols = useMemo(() => [
    { field: 'name', headerName: 'Service', minWidth: 150, cellClass: 'font-semibold', pinned: 'left' },
    { field: 'status', headerName: 'Current Status', cellRenderer: StatusCell, minWidth: 150, filter: true },
    { headerName: 'Cycle Spend vs Monthly Cap', cellRenderer: ProgressCell, minWidth: 230, flex: 1.4,
      valueGetter: (p) => p.data.progress_pct, comparator: (a, b) => a - b },
    { field: 'dormant_days_saved', headerName: 'Dormant Days Saved', type: 'numericColumn', minWidth: 160,
      valueFormatter: (p) => `${p.value.toFixed(1)} d` },
    { field: 'dollars_saved', headerName: 'Total $ Saved vs Flat Plan', type: 'numericColumn', minWidth: 190, valueFormatter: money,
      cellClass: 'text-emerald-300 font-semibold' },
    { field: 'hours_remaining', headerName: 'Expiration Countdown', minWidth: 160, valueFormatter: (p) => fmtHours(p.value) },
  ], [])

  const ledgerCols = useMemo(() => [
    { field: 'sim_time', headerName: 'Timestamp (sim)', minWidth: 140, sort: 'desc',
      valueFormatter: (p) => (p.value ? p.value.replace('T', ' ').slice(0, 16) : '') },
    { field: 'service_name', headerName: 'Service', minWidth: 110 },
    { field: 'buyer_agent_reasoning', headerName: 'Agent Reasoning Summary', flex: 2, minWidth: 260, tooltipField: 'buyer_agent_reasoning' },
    { field: 'pass_type', headerName: 'Selected Tariff', cellRenderer: TierCell, minWidth: 140 },
    { field: 'proration_credit_applied', headerName: 'Proration Discount', type: 'numericColumn', valueFormatter: money, minWidth: 130 },
    { field: 'final_charged_amount', headerName: 'Charged', type: 'numericColumn', valueFormatter: money, minWidth: 100, cellClass: 'font-semibold',
      enableCellChangeFlash: true },
    { field: 'settlement_mode', headerName: 'Rail', cellRenderer: ModeCell, minWidth: 120 },
    { field: 'paypal_order_id', headerName: 'PayPal Order ID', minWidth: 170, cellClass: 'font-mono text-xs' },
    { field: 'paypal_capture_id', headerName: 'Capture ID', minWidth: 170, cellClass: 'font-mono text-xs' },
    { field: 'status', headerName: 'Status', cellRenderer: LedgerStatus, minWidth: 120 },
  ], [])

  const defaultColDef = useMemo(() => ({ sortable: true, resizable: true, filter: true, floatingFilter: false }), [])
  const totalSpend = state.wallet.current_month_spend
  const saved = services.reduce((a, s) => a + s.dollars_saved, 0)
  const dormantDays = services.reduce((a, s) => a + s.dormant_days_saved, 0)
  const sel = ledger.find((l) => l.id === selected)
  let selTrace = []
  try { selTrace = sel ? JSON.parse(sel.agent_trace) : [] } catch { /* ignore */ }

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Kpi icon={Wallet} label="ElasticPay spend" value={`$${totalSpend.toFixed(2)}`} sub={`vs $${state.flat_plan_total.toFixed(2)} on flat plans`} tone="text-indigo-300" />
        <Kpi icon={PiggyBank} label="Saved this cycle" value={`$${saved.toFixed(2)}`} sub="flat plan minus what you actually paid" tone="text-emerald-300" />
        <Kpi icon={CalendarOff} label="Dormant days saved" value={dormantDays.toFixed(0)} sub="service-days you did not pay for" tone="text-sky-300" />
        <Kpi icon={Receipt} label="Budget left" value={`$${state.wallet.budget_left.toFixed(2)}`} sub={`of $${state.wallet.monthly_budget_limit.toFixed(2)} monthly budget`} tone="text-amber-300" />
      </div>

      <section className="card p-4">
        <h3 className="mb-3 text-sm font-semibold uppercase tracking-wider text-slate-300">Subscription & Proration Matrix</h3>
        <div style={{ height: 230 }}>
          <AgGridReact theme={theme} rowData={services} columnDefs={serviceCols} defaultColDef={defaultColDef}
            getRowId={(p) => String(p.data.id)} rowHeight={52} animateRows />
        </div>
      </section>

      <section className="card p-4">
        <h3 className="mb-1 text-sm font-semibold uppercase tracking-wider text-slate-300">Live PayPal Sandbox & Agent Audit Ledger</h3>
        <p className="mb-3 text-xs text-slate-400">Click a row to replay that negotiation's agent trace. Declined purchases are audited too.</p>
        <div style={{ height: 340 }}>
          <AgGridReact theme={theme} rowData={ledger} columnDefs={ledgerCols} defaultColDef={defaultColDef}
            getRowId={(p) => String(p.data.id)} animateRows tooltipShowDelay={200} rowSelection={{ mode: 'singleRow', checkboxes: false, enableClickSelection: true }}
            onRowClicked={(e) => setSelected(e.data.id)} overlayNoRowsTemplate="No transactions yet - launch an app to trigger a JIT unlock" />
        </div>
      </section>

      <div className="grid gap-5 lg:grid-cols-2">
        <section className="card p-4">
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold uppercase tracking-wider text-slate-300"><Bot size={14} /> Live agent telemetry</h3>
          <div className="h-64 space-y-1 overflow-auto font-mono text-xs">
            {telemetry.length === 0 && <div className="text-slate-500">Waiting for agent activity...</div>}
            {[...telemetry].reverse().slice(0, 80).map((e, i) => (
              <div key={i}><span className={AGENT_CLS[e.agent]}>[{e.agent}]</span> {e.text}</div>
            ))}
          </div>
        </section>
        <section className="card p-4">
          <h3 className="mb-3 text-sm font-semibold uppercase tracking-wider text-slate-300">
            {sel ? `Trace replay - ledger #${sel.id}` : 'Receipt & trace replay'}
          </h3>
          {sel ? (
            <div className="h-64 space-y-1 overflow-auto font-mono text-xs">
              {sel.receipt_hash && <div className="break-all text-fuchsia-300">HMAC receipt: {sel.receipt_hash}</div>}
              {selTrace.map((e, i) => (<div key={i}><span className={AGENT_CLS[e.agent]}>[{e.agent}]</span> {e.text}</div>))}
            </div>
          ) : <div className="text-sm text-slate-500">Select a ledger row above.</div>}
        </section>
      </div>
    </div>
  )
}
