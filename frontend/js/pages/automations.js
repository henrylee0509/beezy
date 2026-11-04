import { api } from '../api.js';
import { refreshCounts } from '../app.js';
import { can, errToast, esc, field, fmt, icon, modal, on, skeleton, statusBadge, toast } from '../ui.js';

const ACT_ICON = { ALERT: 'bell', FORECAST: 'finance', DRAFT_PO: 'procurement', RECOMMEND: 'insights', REMINDER: 'contracts',
  PREPARE_PAYROLL: 'payroll', DRAFT_REMINDER_EMAIL: 'contracts' };
const trigLabel = (r) => {
  const p = r.params || {};
  const map = { STOCK_BELOW_PCT: `stock drops below ${p.pct ?? 20}%`, STOCK_CRITICAL: `stock drops below ${p.pct ?? 5}%`,
    INVOICE_OVERDUE: `an invoice is ${p.days ?? 30}+ days overdue`, BILL_DUE_SOON: `a bill is due within ${p.days ?? 7} days`,
    CONTRACT_EXPIRING: 'a contract deadline is near', PAYROLL_DUE: `payday is ${p.days_before ?? 5} days away`,
    EXPENSE_OVER_BUDGET: 'spending nears a budget', REVENUE_BELOW_TARGET: 'revenue falls behind target', DELIVERY_DELAYED: 'a delivery is late' };
  return map[r.trigger_type] || r.trigger_label.toLowerCase();
};

export async function render(el) {
  el.innerHTML = `<div class="page-head"><div><h1>Automations</h1><div class="lead">Rules that watch your business and prepare work. Anything that commits money still goes to the approval queue unless you switch on auto-approval with a spending limit in Settings.</div></div>
    <div class="actions">${can('automations.write') ? `<button class="btn" id="runAll">${icon('automations')} Run all now</button><button class="btn primary" id="newRule">${icon('plus')} New rule</button>` : ''}</div></div>
    <div id="policy"></div><div id="rules" class="grid g2">${skeleton(5)}</div>
    <section class="panel" style="margin-top:16px"><div class="panel-head"><h2>Run history</h2></div><div class="panel-body" id="hist"></div></section>`;
  let data;
  async function load() {
    try { [data] = await Promise.all([api.get('/api/automations')]); } catch (e) { return errToast(e); }
    const s = data.settings;
    el.querySelector('#policy').innerHTML = `<div class="action-card" style="margin:0 0 16px"><span class="bz-hex sm">${icon('shield')}</span><div class="ac-main"><div class="ac-title">Approval policy</div>
      <div class="small muted">${s.auto_po_enabled ? `Automation-drafted orders up to ${fmt.money(s.auto_approve_limit)} are approved automatically.` : 'Every automated purchase order needs a human approval.'}
      Managers can approve orders up to ${fmt.money(s.manager_spending_limit)}; larger orders need the owner. Payroll always needs the owner.</div></div><a class="btn sm ghost" href="#/settings">Change</a></div>`;
    el.querySelector('#rules').innerHTML = data.rules.map(r => `<article class="panel rule ${r.enabled ? '' : 'off'}" style="padding:16px 18px">
      <div class="row"><h3>${esc(r.name)}</h3><span style="margin-left:auto">${r.enabled ? '<span class="badge green">Active</span>' : '<span class="badge">Paused</span>'}</span></div>
      <p class="muted small" style="margin:6px 0 10px">${esc(r.description || '')}</p>
      <div class="flow-label">How it works</div>
      <div class="flow"><span class="step when">${icon('bell')}When ${esc(trigLabel(r))}</span>
        ${r.actions.map(a => `<span class="arr">${icon('chev')}</span><span class="step">${icon(ACT_ICON[a] || 'check')}${esc(data.actions[a] || a)}</span>`).join('')}
        ${r.approval_required ? `<span class="arr">${icon('chev')}</span><span class="step ok">${icon('approvals')}Owner approves</span>` : ''}</div>
      <div class="flow-label">Last run</div>
      <div class="small">${r.last_run ? `${statusBadge(r.last_run.status)} ${esc(r.last_run.summary || '')} <span class="muted">${fmt.datetime(r.last_run.finished_at)}</span>` : '<span class="muted">Not run yet</span>'}</div>
      ${can('automations.write') ? `<div class="row" style="margin-top:12px"><button class="btn sm" data-run="${r.id}">Run now</button><button class="btn sm ghost" data-toggle="${r.id}">${r.enabled ? 'Pause' : 'Activate'}</button><button class="btn sm ghost" data-edit="${r.id}">Edit</button></div>` : ''}
    </article>`).join('');
    const h = await api.get('/api/automations/history?limit=25');
    el.querySelector('#hist').innerHTML = h.runs.length ? `<table class="t"><thead><tr><th>When</th><th>Rule</th><th>Result</th><th>What happened</th></tr></thead><tbody>${h.runs.map(x => `<tr><td class="small num">${fmt.datetime(x.started_at)}</td><td>${esc(x.rule_name)}</td><td>${statusBadge(x.status)}</td>
      <td class="small">${esc(x.summary || '')}${(x.details || []).length ? `<details><summary class="muted">${x.details.length} detail(s)</summary><ul style="padding-left:16px;margin:4px 0">${x.details.map(d => `<li>${esc(d.text)}${d.message ? `<div class="muted">“${esc(d.message)}”</div>` : ''}</li>`).join('')}</ul></details>` : ''}</td></tr>`).join('')}</tbody></table>`
      : '<p class="muted">No runs yet. Rules run on a schedule, when stock changes, or when you click Run now.</p>';
  }
  const showResult = (r) => toast(`${r.rule}: ${r.summary}`, r.status === 'FAILED' ? 'error' : 'ok');
  on(el, 'click', '[data-run]', async (e, b) => { b.disabled = true; try { showResult(await api.post(`/api/automations/${b.dataset.run}/run`)); refreshCounts(); load(); } catch (err) { errToast(err); } b.disabled = false; });
  on(el, 'click', '[data-toggle]', async (e, b) => { try { await api.post(`/api/automations/${b.dataset.toggle}/toggle`); load(); } catch (err) { errToast(err); } });
  el.querySelector('#runAll')?.addEventListener('click', async () => {
    try { const r = await api.post('/api/automations/run-all'); toast(`${r.runs.filter(x => x.status === 'TRIGGERED').length} of ${r.runs.length} rules took action`, 'ok'); refreshCounts(); load(); } catch (e) { errToast(e); }
  });
  const form = (r = {}) => `<div class="form-grid">${field('name', 'Name', { value: r.name || '', required: true, full: true })}${field('description', 'Description', { value: r.description || '', full: true })}
    ${r.id ? '' : field('trigger_type', 'When', { options: Object.entries(data.triggers).map(([k, v]) => [k, v.label]), full: true })}
    ${field('params', 'Trigger settings (JSON)', { value: JSON.stringify(r.params || {}), full: true, hint: 'e.g. {"pct": 15} or {"days": 30}' })}
    <div class="field full"><label>Then</label>${Object.entries(data.actions).map(([k, v]) => `<label class="check"><input type="checkbox" name="a_${k}" ${(r.actions || []).includes(k) ? 'checked' : ''}> ${esc(v)}</label>`).join('')}</div>
    ${field('approval_required', 'Require human approval for anything it prepares', { type: 'checkbox', value: r.approval_required ?? true, full: true })}</div>`;
  const collect = (f) => {
    let params = {};
    try { params = JSON.parse(f.params || '{}'); } catch (e) { throw new Error('Trigger settings must be valid JSON'); }
    return { name: f.name, description: f.description, params, approval_required: !!f.approval_required, actions: Object.keys(data.actions).filter(k => f['a_' + k]), ...(f.trigger_type ? { trigger_type: f.trigger_type } : {}) };
  };
  el.querySelector('#newRule')?.addEventListener('click', async () => {
    const f = await modal({ title: 'New automation', body: form(), submit: 'Create rule' });
    if (!f) return;
    try { await api.post('/api/automations', collect(f)); toast('Rule created', 'ok'); load(); } catch (e) { errToast(e); }
  });
  on(el, 'click', '[data-edit]', async (e, b) => {
    const r = data.rules.find(x => x.id === Number(b.dataset.edit));
    const f = await modal({ title: `Edit ${r.name}`, body: form(r) });
    if (!f) return;
    try { await api.put(`/api/automations/${r.id}`, collect(f)); toast('Saved', 'ok'); load(); } catch (err) { errToast(err); }
  });
  load();
}
