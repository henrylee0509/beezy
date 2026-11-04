import { api } from '../api.js';
import { approvalRow, bindApprovals } from '../components.js';
import { errToast, esc, fmt, on, skeleton, statusBadge } from '../ui.js';

export async function render(el) {
  let status = 'PENDING';
  el.innerHTML = `<div class="page-head"><div><h1>To approve</h1><div class="lead">Everything the agents and automations prepared that commits money. Approval rights are enforced by the server, not by the AI.</div></div>
    <div class="actions"><div class="seg">${[['PENDING', 'Pending'], ['ALL', 'History']].map(([k, l]) => `<button data-s="${k}" class="${k === status ? 'on' : ''}">${l}</button>`).join('')}</div></div></div>
    <section class="panel"><div class="panel-body" id="list">${skeleton(4)}</div></section>`;
  const box = el.querySelector('#list');
  async function load() {
    try {
      const r = await api.get('/api/approvals?status=' + status);
      if (status === 'PENDING') {
        box.innerHTML = r.approvals.length ? r.approvals.map(approvalRow).join('') : `<div class="empty"><h3>You're all caught up</h3><p>Ask the assistant to restock or prepare payroll and the drafts will appear here.</p></div>`;
      } else {
        box.innerHTML = `<table class="t"><thead><tr><th>Requested</th><th>Item</th><th>By</th><th class="right">Amount</th><th>Status</th><th>Decided by</th></tr></thead><tbody>${r.approvals.map(a => `<tr>
          <td class="small num">${fmt.datetime(a.created_at)}</td><td class="strong">${esc(a.title)}<div class="small muted">${esc(a.summary || '')}</div></td><td class="small">${esc(a.requested_by)}</td>
          <td class="right num">${a.amount ? fmt.money(a.amount) : ''}</td><td>${statusBadge(a.status)}</td><td class="small">${esc(a.decided_by || '')}<div class="muted">${a.decided_at ? fmt.datetime(a.decided_at) : ''}</div></td></tr>`).join('')}</tbody></table>`;
      }
    } catch (e) { errToast(e); }
  }
  bindApprovals(box, load);
  on(el, 'click', '[data-s]', (e, b) => { status = b.dataset.s; el.querySelectorAll('[data-s]').forEach(x => x.classList.toggle('on', x === b)); load(); });
  load();
}
