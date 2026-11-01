import { api } from '../api.js';
import { barChart, donut, hbars, lineChart, PALETTE } from '../charts.js';
import { can, errToast, esc, field, fmt, icon, modal, on, skeleton, statusBadge, toast } from '../ui.js';
import { monthLabel } from './overview.js';

export async function render(el, params) {
  let tab = params.get('tab') || 'overview';
  let meta = { income_categories: [], expense_categories: [], definitions: {} };
  try { meta = await api.get('/api/finance/meta'); } catch (e) { /* ignore */ }
  el.innerHTML = `<div class="page-head"><div><h1>Finance</h1><div class="lead">Income, spending, cash and what is coming due. Figures come from recorded transactions (cash basis); this is not certified accounting or tax advice.</div></div>
    <div class="actions"><a class="btn" href="#/assistant?q=${encodeURIComponent('How much money came in and went out over the last 30 days, which expenses increased the most, and is my cash position healthy for the next month?')}">${icon('sparkle')} Ask about cash</a>
    ${can('finance.write') ? `<button class="btn" data-new="INCOME">${icon('plus')} Income</button><button class="btn primary" data-new="EXPENSE">${icon('plus')} Expense</button>` : ''}</div></div>
    <div class="tabs" role="tablist">${[['overview', 'Overview'], ['forecast', 'Cash-flow forecast'], ['transactions', 'Transactions'], ['invoices', 'Invoices & bills']].map(([k, l]) => `<button role="tab" data-tab="${k}" class="${tab === k ? 'on' : ''}">${l}</button>`).join('')}</div>
    <div id="pane"></div>`;
  let pane = el.querySelector('#pane');
  on(el, 'click', '[data-tab]', (e, b) => { tab = b.dataset.tab; el.querySelectorAll('[data-tab]').forEach(x => x.classList.toggle('on', x === b)); show(); });
  on(el, 'click', '[data-new]', (e, b) => txnForm(b.dataset.new));

  let period = 'last30';
  async function overview() {
    pane.innerHTML = skeleton(6);
    const [s, cf] = await Promise.all([api.get('/api/finance/summary?period=' + period), api.get('/api/finance/cash-flow')]);
    const d = s.definitions;
    const kp = (l, v, def, extra = '') => `<div class="kpi"><div class="k-label">${l} <span class="info-tip" title="${esc(def)}">i</span></div><div class="k-value num">${v}</div>${extra}</div>`;
    const thisMonth = new Date().toISOString().slice(0, 7);
    pane.innerHTML = `<div class="row" style="margin-bottom:12px"><div class="seg" role="group" aria-label="Period"><button data-period="last30" class="${period === 'last30' ? 'on' : ''}">Last 30 days</button><button data-period="${thisMonth}" class="${period !== 'last30' ? 'on' : ''}">This month</button></div>
      <span class="muted small">${esc(s.period_label)}: ${fmt.date(s.period_start)} to ${fmt.date(s.period_end)}</span></div>
      <div class="kpis" style="margin-bottom:16px">
        ${kp('Revenue', fmt.money(s.revenue), d.revenue, s.revenue_change_pct !== null ? `<div class="k-delta ${s.revenue_change_pct >= 0 ? 'up' : 'down'}">${fmt.pct(s.revenue_change_pct, true)} vs previous period</div>` : '')}
        ${kp('Expenses', fmt.money(s.expenses), d.expenses, s.expenses_change_pct !== null ? `<div class="k-delta ${s.expenses_change_pct <= 0 ? 'up' : 'down'}">${fmt.pct(s.expenses_change_pct, true)} vs previous period</div>` : '')}
        ${kp('Net operating profit', fmt.money(s.net_operating_profit), d.net_operating_profit)}
        ${kp('Cash balance', fmt.money(s.cash_balance), d.cash_balance)}
        ${kp('Accounts receivable', fmt.money(s.accounts_receivable), d.accounts_receivable, s.overdue_receivables ? `<div class="k-delta down">${fmt.money(s.overdue_receivables)} overdue</div>` : '')}
        ${kp('Accounts payable', fmt.money(s.accounts_payable), d.accounts_payable, s.payables_due_7_days ? `<div class="k-delta" style="color:var(--amber)">${fmt.money(s.payables_due_7_days)} due in 7 days</div>` : '')}
      </div>
      <div class="grid g2">
        <section class="panel"><div class="panel-head"><h2>Monthly revenue, expenses and profit</h2></div><div class="panel-body">${barChart({ labels: cf.monthly.map(m => monthLabel(m.month) + (m.partial ? '*' : '')),
          series: [{ name: 'Revenue', values: cf.monthly.map(m => m.revenue), color: PALETTE[0] }, { name: 'Expenses', values: cf.monthly.map(m => m.expenses), color: '#9aa6bd' },
            { name: 'Net operating profit', values: cf.monthly.map(m => Math.max(0, m.net_operating_profit)), color: PALETTE[1] }] })}</div></section>
        <section class="panel"><div class="panel-head"><h2>Expense breakdown</h2><span class="sub">Last 30 days</span></div><div class="panel-body">${donut(cf.expense_breakdown_30d.map(x => ({ label: x.category, value: x.total })))}</div></section>
        <section class="panel"><div class="panel-head"><h2>Unusual spending</h2><span class="sub">Last 30 days vs the three 30-day periods before</span></div><div class="panel-body">
          ${cf.anomalies.length ? cf.anomalies.map(a => `<div class="alert-row"><span class="sev warning"></span><div><div class="a-title">${esc(a.category)} up ${a.increase_pct}%</div>
            <div class="a-detail">${fmt.money(a.this_period)} vs average ${fmt.money(a.prior_30_day_windows_avg)} (+${fmt.money(a.increase_amount)})</div></div></div>`).join('') : '<p class="muted">No category is unusually high.</p>'}</div></section>
        <section class="panel"><div class="panel-head"><h2>Monthly budgets</h2><span class="sub">This month to date</span></div><div class="panel-body">
          ${cf.budgets.length ? hbars(cf.budgets.map(b => ({ label: `${b.category} (budget ${fmt.money(b.budget, { dp: 0 })})`, value: b.spent, color: b.over ? 'var(--red)' : (b.used_pct > 90 ? 'var(--amber)' : 'var(--green)'),
            tip: `${b.category}: ${fmt.money(b.spent)} of ${fmt.money(b.budget)} (${b.used_pct}%)` })), { format: v => fmt.money(v, { dp: 0 }), max: Math.max(...cf.budgets.map(b => Math.max(b.budget, b.spent))) })
            : '<p class="muted">No budgets set. Add them in Settings.</p>'}</div></section>
      </div>`;
    on(pane, 'click', '[data-period]', (e, b) => { if (period !== b.dataset.period) { period = b.dataset.period; overview(); } });
  }

  let horizon = 30;
  async function forecast() {
    pane.innerHTML = skeleton(6);
    const f = await api.get('/api/finance/forecast?days=' + horizon);
    pane.innerHTML = `<div class="row" style="margin-bottom:12px"><div class="seg">${[30, 60, 90].map(d => `<button data-h="${d}" class="${horizon === d ? 'on' : ''}">${d} days</button>`).join('')}</div></div>
      <div class="kpis" style="margin-bottom:16px">
        <div class="kpi"><div class="k-label">Cash today (recorded)</div><div class="k-value num">${fmt.money(f.starting_cash)}</div></div>
        <div class="kpi"><div class="k-label">Obligations in window</div><div class="k-value num">${fmt.money(f.total_obligations)}</div></div>
        <div class="kpi"><div class="k-label">Customer payments due</div><div class="k-value num">${fmt.money(f.total_expected_receipts)}</div></div>
        <div class="kpi"><div class="k-label">Committed scenario, day ${f.horizon_days}</div><div class="k-value num ${f.projected_committed_balance < 0 ? 'down' : ''}">${fmt.money(f.projected_committed_balance)}</div><div class="small muted">Lowest ${fmt.money(f.lowest_committed_balance)} on ${fmt.date(f.lowest_committed_date)}</div></div>
        <div class="kpi"><div class="k-label">Expected scenario (estimate)</div><div class="k-value num">${fmt.money(f.projected_expected_balance)}</div></div>
      </div>
      <section class="panel" style="margin-bottom:16px"><div class="panel-head"><h2>Projected cash balance</h2><span class="sub">Projections, not guarantees</span></div><div class="panel-body">
        ${lineChart({ labels: f.series.map(p => fmt.date(p.date)), zeroLine: true, series: [{ name: 'Committed (recorded obligations only)', values: f.series.map(p => p.committed), color: PALETTE[0] },
          { name: 'Expected (adds average sales & purchasing)', values: f.series.map(p => p.expected), color: PALETTE[1], dashed: true }] })}</div></section>
      <div class="grid g-3-2">
        <section class="panel"><div class="panel-head"><h2>Upcoming obligations</h2></div><div class="panel-body table-wrap"><table class="t"><thead><tr><th>Due</th><th>Type</th><th>Description</th><th>Basis</th><th class="right">Amount</th></tr></thead><tbody>
          ${f.obligations.map(o => `<tr><td>${fmt.date(o.date)}${o.overdue ? ' <span class="badge red">overdue</span>' : ''}</td><td>${esc(o.type)}</td><td>${esc(o.description)}</td><td class="small muted">${esc(o.basis)}</td><td class="right num">${fmt.money(o.amount)}</td></tr>`).join('') || '<tr><td colspan="5" class="muted">Nothing due in this window.</td></tr>'}
        </tbody></table></div></section>
        <section class="panel"><div class="panel-head"><h2>Assumptions</h2></div><div class="panel-body"><ul style="margin:0;padding-left:18px">${f.assumptions.map(a => `<li style="margin-bottom:6px">${esc(a)}</li>`).join('')}</ul>
          ${f.overdue_receivables_excluded ? `<p class="small" style="margin-top:10px">${fmt.money(f.overdue_receivables_excluded)} of overdue customer invoices is excluded; collecting it would lift both scenarios.</p>` : ''}</div></section>
      </div>`;
    on(pane, 'click', '[data-h]', (e, b) => { horizon = Number(b.dataset.h); forecast(); });
  }

  let filters = { direction: '', category: '', q: '', offset: 0 };
  async function transactions() {
    const qs = new URLSearchParams(Object.entries({ ...filters, limit: 50 }).filter(([, v]) => v !== '' && v !== null)).toString();
    const r = await api.get('/api/finance/transactions?' + qs);
    const cats = [...meta.income_categories, ...meta.expense_categories];
    pane.innerHTML = `<div class="row" style="margin-bottom:12px">
        <select class="input" id="fDir" style="width:auto"><option value="">All</option><option value="INCOME" ${filters.direction === 'INCOME' ? 'selected' : ''}>Income</option><option value="EXPENSE" ${filters.direction === 'EXPENSE' ? 'selected' : ''}>Expenses</option></select>
        <select class="input" id="fCat" style="width:auto"><option value="">All categories</option>${cats.map(c => `<option ${filters.category === c ? 'selected' : ''}>${esc(c)}</option>`).join('')}</select>
        <input class="input" id="fQ" placeholder="Search description or counterparty" value="${esc(filters.q)}" style="max-width:280px">
        <span class="muted small" style="margin-left:auto">${r.total} transactions</span></div>
      <section class="panel"><div class="panel-body table-wrap"><table class="t"><thead><tr><th>Date</th><th>Description</th><th>Counterparty</th><th>Category</th><th>Status</th><th class="right">Amount</th><th></th></tr></thead><tbody>
        ${r.items.map(t => `<tr><td class="num">${fmt.date(t.txn_date)}</td><td>${esc(t.description || '')}${t.document_id ? ' <span class="badge outline">receipt</span>' : ''}</td><td class="small">${esc(t.counterparty || '')}</td>
          <td>${esc(t.category)}</td><td>${statusBadge(t.status)}</td><td class="right num ${t.direction === 'INCOME' ? 'up' : ''}">${t.direction === 'INCOME' ? '+' : '−'}${fmt.money(t.amount)}</td>
          <td class="actions">${can('finance.write') ? `<button class="btn sm ghost" data-edit-txn="${t.id}">Edit</button><button class="btn sm ghost" data-receipt="${t.id}">Receipt</button>` : ''}</td></tr>`).join('')}
      </tbody></table>
      <div class="row" style="margin-top:10px;justify-content:flex-end"><button class="btn sm" id="prev" ${filters.offset ? '' : 'disabled'}>Previous</button><button class="btn sm" id="next" ${filters.offset + 50 < r.total ? '' : 'disabled'}>Next</button></div></div></section>`;
    const re = () => { filters.offset = 0; transactions(); };
    pane.querySelector('#fDir').onchange = (e) => { filters.direction = e.target.value; re(); };
    pane.querySelector('#fCat').onchange = (e) => { filters.category = e.target.value; re(); };
    let t;
    pane.querySelector('#fQ').oninput = (e) => { clearTimeout(t); t = setTimeout(() => { filters.q = e.target.value; re(); }, 350); };
    pane.querySelector('#prev').onclick = () => { filters.offset = Math.max(0, filters.offset - 50); transactions(); };
    pane.querySelector('#next').onclick = () => { filters.offset += 50; transactions(); };
    on(pane, 'click', '[data-edit-txn]', async (e, b) => {
      const tx = r.items.find(x => x.id === Number(b.dataset.editTxn));
      const cats2 = tx.direction === 'INCOME' ? meta.income_categories : meta.expense_categories;
      const f = await modal({ title: 'Edit transaction', body: `<div class="form-grid">${field('category', 'Category', { options: cats2, value: tx.category })}${field('amount', 'Amount', { type: 'number', step: '0.01', value: tx.amount })}
        ${field('description', 'Description', { value: tx.description || '', full: true })}${field('status', 'Status', { options: ['PAID', 'PENDING'], value: tx.status })}${field('txn_date', 'Date', { type: 'date', value: tx.txn_date })}</div>` });
      if (!f) return;
      try { await api.put(`/api/finance/transactions/${tx.id}`, { ...f, amount: Number(f.amount) }); toast('Saved', 'ok'); transactions(); } catch (err) { errToast(err); }
    });
    on(pane, 'click', '[data-receipt]', async (e, b) => {
      const f = await modal({ title: 'Attach receipt', body: `<div class="field"><label for="rf">Receipt file (PDF, image or text)</label><input class="input" id="rf" type="file" name="file" accept=".pdf,.png,.jpg,.jpeg,.webp,.txt" required></div>`, submit: 'Upload',
        onOpen: (form) => { form._file = form.querySelector('#rf'); } });
      if (!f) return;
      const input = document.querySelector('#rf');
      const fd = new FormData();
      if (!f.file || !f.file.size) return toast('Choose a file', 'error');
      fd.append('file', f.file);
      try { await api.upload(`/api/finance/transactions/${b.dataset.receipt}/receipt`, fd); toast('Receipt attached', 'ok'); transactions(); } catch (err) { errToast(err); }
      void input;
    });
  }

  async function invoices() {
    const r = await api.get('/api/finance/invoices');
    const table = (rows, kind) => `<table class="t"><thead><tr><th>Number</th><th>${kind === 'r' ? 'Customer' : 'Supplier'}</th><th>Issued</th><th>Due</th><th class="right">Amount</th><th></th></tr></thead><tbody>
      ${rows.map(i => `<tr><td class="strong">${esc(i.number)}</td><td>${esc(i.counterparty)}</td><td>${fmt.date(i.issue_date)}</td>
        <td>${fmt.date(i.due_date)} ${kind === 'r' ? (i.days_overdue ? `<span class="badge ${i.seriously_overdue ? 'red' : 'amber'}">${i.days_overdue} days overdue</span>` : '') : (i.days_until_due <= 7 ? `<span class="badge amber">due ${fmt.rel(i.days_until_due)}</span>` : '')}</td>
        <td class="right num">${fmt.money(i.amount)}</td><td class="actions">${can('finance.write') ? `<button class="btn sm" data-pay="${i.id}">${kind === 'r' ? 'Mark received' : 'Mark paid'}</button>` : ''}</td></tr>`).join('') || '<tr><td colspan="6" class="muted">None open.</td></tr>'}</tbody></table>`;
    pane.innerHTML = `${can('finance.write') ? `<div class="row" style="margin-bottom:12px"><button class="btn" id="newInv">${icon('plus')} Add invoice or bill</button></div>` : ''}
      <div class="stack"><section class="panel"><div class="panel-head"><h2>Customer invoices (receivable)</h2><span class="sub">${fmt.money(r.receivables.total_open)} open · ${fmt.money(r.receivables.total_overdue)} overdue</span>
        <div class="tools"><a class="btn sm" href="#/assistant?q=${encodeURIComponent('Draft polite payment reminders for my overdue customer invoices and tell me how collecting them changes my cash forecast.')}">${icon('sparkle')} Draft reminders</a></div></div>
        <div class="panel-body table-wrap">${table(r.receivables.invoices, 'r')}</div></section>
      <section class="panel"><div class="panel-head"><h2>Supplier bills (payable)</h2><span class="sub">${fmt.money(r.payables.total_open)} open</span></div><div class="panel-body table-wrap">${table(r.payables.invoices, 'p')}</div></section></div>`;
    on(pane, 'click', '[data-pay]', async (e, b) => { try { await api.post(`/api/finance/invoices/${b.dataset.pay}/pay`); toast('Recorded', 'ok'); invoices(); } catch (err) { errToast(err); } });
    pane.querySelector('#newInv')?.addEventListener('click', async () => {
      const today = new Date().toISOString().slice(0, 10);
      const f = await modal({ title: 'Add invoice or bill', body: `<div class="form-grid">${field('kind', 'Type', { options: [['RECEIVABLE', 'Customer invoice (money in)'], ['PAYABLE', 'Supplier bill (money out)']] })}
        ${field('number', 'Number', { required: true })}${field('counterparty', 'Customer / supplier', { required: true })}${field('amount', 'Amount', { type: 'number', step: '0.01', required: true })}
        ${field('issue_date', 'Issue date', { type: 'date', value: today })}${field('due_date', 'Due date', { type: 'date', value: today })}</div>` });
      if (!f) return;
      try { await api.post('/api/finance/invoices', { ...f, amount: Number(f.amount) }); toast('Added', 'ok'); invoices(); } catch (err) { errToast(err); }
    });
  }

  async function txnForm(direction) {
    const cats = direction === 'INCOME' ? meta.income_categories : meta.expense_categories;
    const f = await modal({ title: direction === 'INCOME' ? 'Record income' : 'Record expense', body: `<div class="form-grid">
      ${field('amount', 'Amount', { type: 'number', step: '0.01', required: true })}${field('txn_date', 'Date', { type: 'date', value: new Date().toISOString().slice(0, 10) })}
      ${field('description', 'Description', { full: true, required: true })}${field('category', 'Category', { options: [['', 'Suggest from description'], ...cats] })}
      ${field('counterparty', direction === 'INCOME' ? 'Customer' : 'Paid to')}${field('payment_method', 'Payment method', { options: ['Bank transfer', 'Card', 'Cash', 'Card / cash', 'Other'] })}
      ${field('status', 'Status', { options: ['PAID', 'PENDING'] })}</div>`, submit: 'Record' });
    if (!f) return;
    try {
      const t = await api.post('/api/finance/transactions', { ...f, direction, amount: Number(f.amount), category: f.category || null });
      toast(`Recorded as ${t.category}`, 'ok');
      show();
    } catch (e) { errToast(e); }
  }

  async function show() {
    const fresh = pane.cloneNode(false);  // drop listeners bound by the previous view
    pane.replaceWith(fresh);
    pane = fresh;
    try {
      if (tab === 'overview') await overview();
      else if (tab === 'forecast') await forecast();
      else if (tab === 'transactions') await transactions();
      else await invoices();
    } catch (e) { pane.innerHTML = `<p class="muted">${esc(e.message)}</p>`; }
  }
  show();
}
