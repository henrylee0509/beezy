import { api } from '../api.js';
import { barChart, donut, hbars, lineChart, PALETTE } from '../charts.js';
import { approvalRow, bindApprovals } from '../components.js';
import { aiSource, can, errToast, esc, fmt, icon, md, skeleton, state } from '../ui.js';

export async function render(el) {
  const me = state.me;
  const hour = new Date().getHours();
  const greet = hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening';
  el.innerHTML = `<div class="page-head"><div><h1>${greet}, ${esc(me.user.name.split(' ')[0])}</h1>
    <div class="lead">Here is how ${esc(me.business.name)} is doing today.</div></div>
    <div class="actions"><a class="btn" href="#/assistant?q=${encodeURIComponent('Give me a health check of my business. Check my inventory, upcoming expenses, payroll and cash flow. Tell me what I should prioritise.')}">${icon('sparkle')} Run a health check</a></div></div>
    <div class="grid g-2-1" style="margin-bottom:16px">
      <section class="panel" id="briefing">${can('finance.read') ? `<div class="briefing">${skeleton(4)}</div>` : ''}</section>
      <section class="panel" id="decisions"><div class="panel-body">${skeleton(3)}</div></section>
    </div>
    <div id="kpis" style="margin-bottom:16px"></div>
    <div id="charts" class="grid g2"></div>
    <section class="panel" style="margin-top:16px"><div class="panel-head"><h2>Business alerts</h2><span class="sub" id="alertCount"></span></div><div class="panel-body"><div class="alerts" id="alerts"></div></div></section>`;
  if (!can('finance.read')) document.getElementById('briefing').remove(), el.querySelector('.g-2-1').style.gridTemplateColumns = '1fr';

  const loadDecisions = async () => {
    const box = document.getElementById('decisions');
    if (!can('approvals.read')) { box.innerHTML = `<div class="panel-body muted">Approvals are handled by managers and the owner.</div>`; return; }
    try {
      const r = await api.get('/api/approvals');
      box.innerHTML = `<div class="panel-head"><h2>Needs your decision</h2><span class="sub">${r.approvals.length} pending</span><div class="tools"><a class="small" href="#/approvals">All approvals</a></div></div>
        <div class="panel-body">${r.approvals.length ? r.approvals.slice(0, 4).map(approvalRow).join('') : `<p class="muted" style="margin:0">Nothing is waiting for approval. Agents will put purchase orders and payroll here before anything is committed.</p>`}</div>`;
    } catch (e) { box.innerHTML = `<div class="panel-body muted">${esc(e.message)}</div>`; }
  };
  bindApprovals(document.getElementById('decisions'), () => { loadDecisions(); load(); });
  loadDecisions();

  const loadBriefing = async (refresh = false) => {
    const box = document.getElementById('briefing');
    if (!box) return;
    box.innerHTML = `<div class="briefing">${skeleton(4)}</div>`;
    try {
      const b = await api.get('/api/dashboard/briefing' + (refresh ? '?refresh=1' : ''));
      box.innerHTML = `<div class="panel-head"><h2>Your business at a glance</h2><span class="sub">Generated ${fmt.datetime(b.generated_at)}</span>
        <div class="tools"><button class="btn sm ghost" id="refreshBriefing">${icon('refresh')} Regenerate</button></div></div>
        <div class="briefing"><div class="ai-prose">${md(b.content)}</div>${aiSource(b.source, b.verification)}</div>`;
      document.getElementById('refreshBriefing').onclick = () => loadBriefing(true);
    } catch (e) { box.innerHTML = `<div class="panel-body muted">Briefing unavailable: ${esc(e.message)}</div>`; }
  };
  loadBriefing();

  async function load() {
    let d;
    try { d = await api.get('/api/dashboard/overview'); } catch (e) { return errToast(e); }
    const k = d.kpis, defs = d.definitions;
    const kp = (label, value, delta, def, deltaGoodUp = true) => `<div class="kpi"><div class="k-label">${esc(label)}${def ? ` <span class="info-tip" title="${esc(def)}">i</span>` : ''}</div>
      <div class="k-value num">${value}</div>${delta !== undefined && delta !== null ? `<div class="k-delta ${(delta >= 0) === deltaGoodUp ? 'up' : 'down'}">${fmt.pct(delta, true)} vs prior 30 days</div>` : ''}</div>`;
    let html = '';
    if (k.revenue !== undefined) {
      html += kp('Revenue, last 30 days', fmt.money(k.revenue, { dp: 0 }), k.revenue_change_pct, defs.revenue)
        + kp('Expenses, last 30 days', fmt.money(k.expenses, { dp: 0 }), k.expenses_change_pct, defs.expenses, false)
        + kp('Net operating profit', fmt.money(k.net_operating_profit, { dp: 0 }), null, defs.net_operating_profit)
        + kp('Cash balance', fmt.money(k.cash_balance, { dp: 0 }), null, defs.cash_balance)
        + kp('Outstanding invoices', `${k.outstanding_invoices} · ${fmt.money(k.accounts_receivable, { dp: 0 })}`, null, defs.accounts_receivable);
    }
    html += kp('Inventory value', fmt.money(k.inventory_value, { dp: 0 }), null, 'Current stock valued at unit cost.')
      + kp('Below stock threshold', `${k.low_stock_count} product${k.low_stock_count === 1 ? '' : 's'}`)
      + kp('Open purchase orders', k.pending_purchase_orders)
      + kp('Active employees', k.active_employees);
    document.getElementById('kpis').innerHTML = `<div class="kpis">${html}</div>`;

    const c = d.charts;
    const panel = (title, sub, body, extra = '') => `<section class="panel"><div class="panel-head"><h2>${title}</h2><span class="sub">${sub}</span>${extra}</div><div class="panel-body">${body}</div></section>`;
    let ch = '';
    if (c.revenue_vs_expenses) {
      ch += panel('Revenue vs expenses', 'Last 6 months', barChart({ labels: c.revenue_vs_expenses.map(m => monthLabel(m.month) + (m.partial ? '*' : '')),
        series: [{ name: 'Revenue', values: c.revenue_vs_expenses.map(m => m.revenue), color: PALETTE[0] },
                 { name: 'Expenses', values: c.revenue_vs_expenses.map(m => m.expenses), color: '#cfc8b8' }] }) + '<div class="small muted">* month to date</div>');
      ch += panel('Cash balance', 'Last 60 days, recorded', lineChart({ labels: c.cash_trend.map(p => fmt.date(p.date)), series: [{ name: 'Cash', values: c.cash_trend.map(p => p.balance), color: PALETTE[1], area: true }] }));
      ch += panel('Daily sales', 'Last 30 days', lineChart({ labels: c.daily_sales.map(p => fmt.date(p.date)), series: [{ name: 'Sales', values: c.daily_sales.map(p => p.sales), color: PALETTE[0] }] }));
      ch += panel('Where money went', 'Expenses, last 30 days', c.expense_breakdown.length ? donut(c.expense_breakdown.map(e => ({ label: e.category, value: e.total })))
        : '<p class="muted">No expenses recorded yet this month.</p>');
    }
    ch += panel('Stock levels', 'Share of max capacity; the marker is the alert threshold', hbars(c.inventory_levels.map(p => ({ label: p.name, value: p.stock_pct, mark: 20,
      color: p.level === 'HEALTHY' ? 'var(--green)' : p.level === 'LOW' ? 'var(--amber)' : 'var(--red)' })), { format: v => v.toFixed(0) + '%', max: 100 }),
      `<div class="tools"><a class="small" href="#/inventory">Inventory</a></div>`);
    ch += panel('Stock consumed', 'Last 30 days, at cost', hbars(c.top_products.map((p, i) => ({ label: p.name, value: p.consumption_value, color: PALETTE[(i + 1) % PALETTE.length],
      tip: `${p.name}: ${fmt.num(p.units, 1)} units · ${fmt.money(p.consumption_value)}` })), { format: v => fmt.money(v, { dp: 0 }) }));
    if (c.payroll_monthly) {
      ch += panel('Payroll expenditure', 'Salaries paid per month', barChart({ labels: c.payroll_monthly.map(m => monthLabel(m.month)), series: [{ name: 'Salaries', values: c.payroll_monthly.map(m => m.salaries), color: PALETTE[3] }], height: 180 }));
      ch += panel('Inventory value by category', 'At cost', donut(c.inventory_by_category.map(x => ({ label: x.category, value: x.value }))));
    }
    document.getElementById('charts').innerHTML = ch;

    document.getElementById('alertCount').textContent = `${d.alerts.length} open`;
    document.getElementById('alerts').innerHTML = d.alerts.length ? d.alerts.map(a => `<a class="alert-row" href="${a.link}"><span class="sev ${a.severity}"></span>
      <div><div class="a-title">${esc(a.title)}</div><div class="a-detail">${esc(a.detail)}</div></div></a>`).join('') : '<p class="muted">No alerts. Everything is within thresholds.</p>';
  }
  load();
}

export function monthLabel(m) {
  const [y, mo] = m.split('-').map(Number);
  return new Date(y, mo - 1, 1).toLocaleDateString(undefined, { month: 'short' });
}
