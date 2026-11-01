import { api, setToken } from '../api.js';
import { rerenderShell } from '../app.js';
import { can, confirmBox, errToast, esc, field, fmt, skeleton, state, toast } from '../ui.js';

export async function render(el) {
  el.innerHTML = `<div class="page-head"><div><h1>Settings</h1><div class="lead">Thresholds, approval limits and the AI connection.</div></div></div><div id="s">${skeleton(6)}</div>`;
  let d;
  try { d = await api.get('/api/business'); } catch (e) { return errToast(e); }
  const s = d.settings, b = d.business, owner = can('settings.write');
  const dis = owner ? '' : 'disabled';
  el.querySelector('#s').innerHTML = `<form id="f" class="stack">
    <section class="panel"><div class="panel-head"><h2>Business</h2></div><div class="panel-body form-grid">
      ${field('name', 'Business name', { value: b.name, attrs: dis })}${field('business_type', 'Business type', { value: b.business_type, attrs: dis })}
      ${field('currency', 'Currency code', { value: b.currency, attrs: dis + ' maxlength="3"' })}${field('jurisdiction', 'Payroll jurisdiction', { value: b.jurisdiction, hint: 'Used to label payroll rules; configure the rules themselves under Payroll', attrs: dis })}</div></section>
    <section class="panel"><div class="panel-head"><h2>Inventory thresholds</h2><span class="sub">% of maximum capacity</span></div><div class="panel-body form-grid">
      ${field('low_stock_pct', 'Low stock below', { type: 'number', step: '0.5', value: s.low_stock_pct, attrs: dis })}${field('urgent_stock_pct', 'Urgent below', { type: 'number', step: '0.5', value: s.urgent_stock_pct, attrs: dis })}
      ${field('critical_stock_pct', 'Critical below', { type: 'number', step: '0.5', value: s.critical_stock_pct, attrs: dis })}${field('safety_stock_days', 'Safety stock (days)', { type: 'number', value: s.safety_stock_days, attrs: dis })}
      ${field('forecast_window_days', 'Forecast window (days)', { type: 'number', value: s.forecast_window_days, attrs: dis, hint: 'Moving-average window for demand' })}</div></section>
    <section class="panel"><div class="panel-head"><h2>Spending &amp; approvals</h2></div><div class="panel-body form-grid">
      ${field('manager_spending_limit', `Managers can approve orders up to (${esc(b.currency)})`, { type: 'number', step: '0.01', value: s.manager_spending_limit, attrs: dis })}
      ${field('auto_approve_limit', `Auto-approve automation orders up to (${esc(b.currency)})`, { type: 'number', step: '0.01', value: s.auto_approve_limit, attrs: dis, hint: 'Only applies when auto-approval is switched on' })}
      ${field('auto_po_enabled', 'Allow automations to auto-approve purchase orders within the limit above', { type: 'checkbox', value: !!s.auto_po_enabled, full: true, attrs: dis })}</div></section>
    <section class="panel"><div class="panel-head"><h2>Payroll, invoices &amp; contracts</h2></div><div class="panel-body form-grid">
      ${field('payroll_day', 'Payday (day of month)', { type: 'number', value: s.payroll_day, attrs: dis })}${field('invoice_overdue_days', 'Flag customer invoices overdue by more than (days)', { type: 'number', value: s.invoice_overdue_days, attrs: dis })}
      ${field('contract_reminder_days', 'Contract reminders (days before expiry)', { value: s.contract_reminder_days, attrs: dis, hint: 'Comma separated, e.g. 60,30,7,0' })}
      ${field('budgets', 'Monthly budgets (JSON)', { value: JSON.stringify(s.budgets || {}), attrs: dis, hint: 'e.g. {"Utilities": 1000, "Marketing": 800}' })}</div></section>
    ${owner ? '<div class="row"><button class="btn primary" type="submit">Save settings</button></div>' : '<p class="muted">Only the owner can change settings.</p>'}
  </form>
  <section class="panel" style="margin-top:16px"><div class="panel-head"><h2>AI connection</h2></div><div class="panel-body">
    <dl class="kv"><dt>Mode</dt><dd>${d.ai.mode === 'real' ? '<span class="badge teal">Live: Meta Muse Spark</span>' : '<span class="badge amber">Mock (no API key)</span>'}</dd><dt>Reasoning model</dt><dd>${esc(d.ai.model)}</dd>
    <dt>Voice model</dt><dd>${esc(d.ai.asr_model)}</dd><dt>Endpoint</dt><dd>${esc(d.ai.base_url)}</dd><dt>API key</dt><dd>${d.ai.key_configured ? 'Configured on the server' : 'Not set: add MODEL_API_KEY to .env and restart'}</dd></dl>
    <p class="small muted" style="margin-top:8px">The key lives only on the server and is never sent to the browser.</p></div></section>
  ${d.members.length ? `<section class="panel" style="margin-top:16px"><div class="panel-head"><h2>Team &amp; roles</h2></div><div class="panel-body"><table class="t"><tbody>${d.members.map(m => `<tr><td class="strong">${esc(m.name)}</td><td class="muted">${esc(m.email)}</td><td>${esc(m.role.toLowerCase())}</td></tr>`).join('')}</tbody></table></div></section>` : ''}
  ${owner && b.is_demo ? `<section class="panel" style="margin-top:16px"><div class="panel-head"><h2>Demo data</h2></div><div class="panel-body"><p class="muted">Restore Brew &amp; Bean Cafe to its starting state (useful before recording a demo). Every change you made will be lost.</p><button class="btn danger" id="reset">Reset demo data</button></div></section>` : ''}`;
  const form = el.querySelector('#f');
  form.onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(form);
    const body = {};
    for (const [k, v] of fd.entries()) body[k] = v;
    for (const k of ['low_stock_pct', 'urgent_stock_pct', 'critical_stock_pct', 'safety_stock_days', 'forecast_window_days', 'manager_spending_limit', 'auto_approve_limit', 'payroll_day', 'invoice_overdue_days']) body[k] = Number(body[k]);
    body.auto_po_enabled = form.querySelector('[name=auto_po_enabled]').checked;
    try { body.budgets = JSON.parse(body.budgets || '{}'); } catch (err) { return toast('Budgets must be valid JSON', 'error'); }
    body.currency = String(body.currency).toUpperCase();
    try {
      await api.put('/api/business/settings', body);
      state.me.business = { ...state.me.business, name: body.name, currency: body.currency };
      toast('Settings saved', 'ok');
      rerenderShell(await api.get('/api/auth/me'));
    } catch (err) { errToast(err); }
  };
  el.querySelector('#reset')?.addEventListener('click', async () => {
    if (!await confirmBox('Reset demo data', 'This deletes all changes to Brew & Bean Cafe and reseeds fresh demo data.', 'Reset', true)) return;
    try { const r = await api.post('/api/business/reset-demo'); setToken(r.token); toast('Demo data reset', 'ok'); location.hash = '#/overview'; rerenderShell(r); } catch (err) { errToast(err); }
  });
  void fmt;
}
