// Shared business components: approval cards & handlers.
import { api } from './api.js';
import { refreshCounts } from './app.js';
import { errToast, esc, fmt, on, statusBadge, toast } from './ui.js';

export function approvalRow(a) {
  return `<div class="action-card" data-approval-row="${a.id}">
    <div class="ac-main">
      <div class="ac-title">${esc(a.title)}</div>
      <div class="small muted">${esc(a.summary || '')}</div>
      <div class="small muted">Requested by ${esc(a.requested_by)} · needs ${esc(a.required_role.toLowerCase())} approval</div>
    </div>
    ${a.amount ? `<span class="num strong">${fmt.money(a.amount)}</span>` : ''}
    ${a.status === 'PENDING' ? (a.can_decide === false ? `<span class="badge amber">Waiting for ${esc(a.required_role.toLowerCase())}</span>` :
      `<div class="row"><button class="btn sm" data-reject="${a.id}">Reject</button><button class="btn sm primary" data-approve="${a.id}">Approve</button></div>`) : statusBadge(a.status)}
  </div>`;
}

export function bindApprovals(root, after) {
  on(root, 'click', '[data-approve]', async (e, el) => {
    el.disabled = true;
    try {
      const r = await api.post(`/api/approvals/${el.dataset.approve}/approve`);
      toast(r.kind === 'PURCHASE_ORDER' ? 'Approved — order submitted to supplier (simulated)' : 'Approved', 'ok');
      refreshCounts();
      after && after(r);
    } catch (err) { errToast(err); el.disabled = false; }
  });
  on(root, 'click', '[data-reject]', async (e, el) => {
    el.disabled = true;
    try {
      await api.post(`/api/approvals/${el.dataset.reject}/reject`, { reason: 'Rejected from BusinessPilot' });
      toast('Rejected');
      refreshCounts();
      after && after();
    } catch (err) { errToast(err); el.disabled = false; }
  });
}

export const DEPTS = {
  'Inventory Agent': { key: 'inventory', color: '#0d8a80', icon: 'inventory', short: 'Inventory' },
  'Procurement Agent': { key: 'procurement', color: '#4b4fc4', icon: 'procurement', short: 'Procurement' },
  'Finance Agent': { key: 'finance', color: '#2456d3', icon: 'finance', short: 'Finance' },
  'HR & Payroll Agent': { key: 'hr', color: '#7a3e8f', icon: 'payroll', short: 'HR & Payroll' },
  'Contract Agent': { key: 'contracts', color: '#8a5a2b', icon: 'contracts', short: 'Contracts' },
  'Business Intelligence Agent': { key: 'bi', color: '#14213d', icon: 'bi', short: 'Intelligence' },
};
