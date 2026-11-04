// Visual answer cards for the assistant, built from verified tool data (backend/app/ai/cards.py).
import { esc, fmt, icon, statusBadge } from './ui.js';

let uid = 0;

// A honeycomb cell that fills with honey up to pct; orange/red when low.
export function hexGauge(pct, size = 46, level) {
  const id = `hx${++uid}`;
  const p = Math.max(0, Math.min(100, pct || 0));
  const color = level === 'HEALTHY' || p >= 20 ? 'var(--honey)' : p >= 10 ? '#e8851f' : 'var(--red)';
  const h = size * 1.1547, top = h * (1 - p / 100);
  const pts = `${size / 2},0 ${size},${h / 4} ${size},${(3 * h) / 4} ${size / 2},${h} 0,${(3 * h) / 4} 0,${h / 4}`;
  return `<svg class="hexg" width="${size}" height="${h.toFixed(1)}" viewBox="0 0 ${size} ${h.toFixed(1)}" aria-label="${p}% stocked">
    <defs><clipPath id="${id}"><polygon points="${pts}"/></clipPath></defs>
    <polygon points="${pts}" fill="var(--surface-2)"/>
    <rect x="0" y="${top.toFixed(1)}" width="${size}" height="${(h - top).toFixed(1)}" fill="${color}" clip-path="url(#${id})"/>
    <polygon points="${pts}" fill="none" stroke="var(--ink)" stroke-opacity=".18" stroke-width="1.5"/>
    <text x="50%" y="56%" text-anchor="middle" dominant-baseline="middle" class="hexg-t">${Math.round(p)}%</text></svg>`;
}

const head = (ic, title, sub = '', right = '') => `<div class="bz-head"><span class="bz-hex">${icon(ic)}</span>
  <div class="bz-ttl"><b>${esc(title)}</b>${sub ? `<span>${esc(sub)}</span>` : ''}</div>${right}</div>`;

function pulse(c) {
  return `<section class="bz-card">${head('overview', c.title)}<div class="bz-tiles">${c.tiles.map(t => `
    <div class="bz-tile ${t.tone}"><div class="l">${esc(t.label)}</div><div class="v num">${fmt.money(t.value, { dp: 0 })}</div>
      <div class="s">${t.delta !== undefined && t.delta !== null ? `<span class="${t.delta >= 0 ? 'up' : 'down'}">${fmt.pct(t.delta, true)}</span> vs prior period` : esc(t.sub || '')}</div></div>`).join('')}</div></section>`;
}

function stock(c, live = {}) {
  return `<section class="bz-card">${head('inventory', c.title, c.subtitle || '', c.total ? `<div class="bz-total"><span>New orders</span><b class="num">${fmt.money(c.total)}</b></div>` : '')}
    <div class="bz-stock">${c.rows.map(r => `<div class="bz-srow">
      ${hexGauge(r.stock_pct, 46, r.level)}
      <div class="bz-sp"><b>${esc(r.product)}</b><span>${r.current !== undefined ? `${fmt.num(r.current, 1)} of ${fmt.num(r.max)} ${esc(r.unit || '')}` : ''}</span></div>
      <div class="bz-cover ${r.cover !== null && r.cover !== undefined && r.cover <= 5 ? 'hot' : ''}"><b class="num">${r.cover ?? '—'}</b><span>days left</span></div>
      <div class="bz-sup">${r.on_order ? `<span class="badge green">On order · ${esc(r.on_order)}</span>` : r.supplier ? (r.quantity ? `<span class="muted small">Order</span>
        <b class="num">${fmt.num(r.quantity)} ${esc(r.unit || '')}</b><span class="muted small">from ${esc(r.supplier)}</span>` : `<span class="muted small">Supplier</span><b>${esc(r.supplier)}</b>`) + `${r.drafted ? `<span class="badge teal" style="margin-top:3px;align-self:flex-start">Drafted · ${esc(r.drafted)}</span>` : ''}` : '<span class="muted small">No supplier</span>'}</div>
      <div class="bz-cost num">${r.cost && !r.on_order ? fmt.money(r.cost) : ''}</div>
      ${r.why && !r.on_order ? `<div class="bz-why">${icon('sparkle')}${esc(r.why)}</div>` : ''}</div>`).join('')}</div>
    ${restockCta(c, live)}</section>`;
}

// Offer to draft the orders when the answer only looked at stock; show what is already waiting otherwise.
function restockCta(c, live) {
  if (c.rows.some(r => r.drafted)) return '';   // this answer already drafted the orders; its orders card has the buttons
  const todo = c.rows.filter(r => r.product_id && r.supplier && !r.on_order && !r.drafted);
  const waiting = Object.values(live.approvals || {}).filter(a => a.kind === 'PURCHASE_ORDER' && a.status === 'PENDING');
  if (waiting.length) {
    const ids = waiting.filter(a => a.can_decide !== false).map(a => a.id);
    const sum = waiting.reduce((t, a) => t + (a.amount || 0), 0);
    return `<div class="bz-cta wait">${icon('approvals')}<div><b>${waiting.length} restock order${waiting.length > 1 ? 's are' : ' is'} waiting for your approval</b>
      <span>Nothing is sent to suppliers until you approve. You can also review them in To approve.</span></div>
      ${ids.length ? `<button class="btn primary" data-approve-all="${ids.join(',')}">${icon('check')} Approve all · ${fmt.money(sum, { dp: 0 })}</button>` : `<a class="btn" href="#/approvals">Open To approve</a>`}</div>`;
  }
  if (!todo.length || !live.canRestock) return '';
  const sum = todo.reduce((t, r) => t + (r.cost || 0), 0);
  return `<div class="bz-cta">${icon('procurement')}<div><b>Restock ${todo.length === 1 ? esc(todo[0].product) : `these ${todo.length} items`} now?</b>
    <span>Beezy drafts the orders with the suppliers above. They wait in To approve until you say yes.</span></div>
    <button class="btn primary" data-restock="${todo.map(r => r.product_id).join(',')}">${icon('procurement')} Prepare orders${sum ? ` · ${fmt.money(sum, { dp: 0 })}` : ''}</button></div>`;
}

function runway(c) {
  const total = Math.max(c.cash + (c.receipts || 0), c.outflows, 1);
  const segs = c.segments.map((s, i) => `<i style="width:${(s.value / total * 100).toFixed(2)}%;background:${['#16140f', '#57534e', '#7a5c1e', '#a8a29e', 'var(--honey)'][i % 5]}" data-tip="${esc(`${s.label}: ${fmt.money(s.value)}`)}"></i>`).join('');
  return `<section class="bz-card">${head('finance', c.title, 'Committed outflows against cash on hand')}
    <div class="bz-run">
      <div class="bz-runrow"><span class="lab">Cash + receipts due</span><div class="bar"><i style="width:${((c.cash + (c.receipts || 0)) / total * 100).toFixed(2)}%;background:var(--green)"></i></div><b class="num">${fmt.money(c.cash + (c.receipts || 0), { dp: 0 })}</b></div>
      <div class="bz-runrow"><span class="lab">Committed outflows</span><div class="bar">${segs}</div><b class="num">${fmt.money(c.outflows, { dp: 0 })}</b></div>
      <div class="legend">${c.segments.map((s, i) => `<span><i style="background:${['#16140f', '#57534e', '#7a5c1e', '#a8a29e', 'var(--honey)'][i % 5]}"></i>${esc(s.label)} ${fmt.money(s.value, { dp: 0 })}</span>`).join('')}</div>
      <div class="bz-verdict ${c.affordable ? 'ok' : 'bad'}">${icon(c.affordable ? 'check' : 'bell')}<div><b>${c.affordable ? 'Affordable' : 'Shortfall'}: ${fmt.money(c.remaining)} left</b>
        <span>before new sales (estimate)${c.upside ? `. Collecting overdue invoices adds ${fmt.money(c.upside, { dp: 0 })}.` : ''}</span></div></div>
    </div></section>`;
}

function payroll(c) {
  return `<section class="bz-card">${head('payroll', c.title, c.pay_date ? `Pay date ${fmt.date(c.pay_date)}` : '', `<span style="margin-left:auto">${statusBadge(c.status === 'ESTIMATE' ? 'DRAFT' : c.status).replace('Draft', 'Estimate')}</span>`)}
    <div class="bz-tiles three"><div class="bz-tile"><div class="l">Gross pay</div><div class="v num">${fmt.money(c.gross, { dp: 0 })}</div></div>
      <div class="bz-tile"><div class="l">Net pay</div><div class="v num">${fmt.money(c.net, { dp: 0 })}</div></div>
      <div class="bz-tile"><div class="l">Employer cost</div><div class="v num">${fmt.money(c.employer, { dp: 0 })}</div></div></div>
    ${c.issues.length ? `<div class="bz-issues">${c.issues.map(i => `<div>${icon('bell')}<b>${esc(i.who || '')}</b> ${esc(i.issue)}</div>`).join('')}</div>` : '<div class="bz-issues ok">No blocking issues found.</div>'}</section>`;
}

// Horizontal deadline timeline: today to +90 days, a honeycomb marker per deadline.
export function contractTimeline(items, days = 90) {
  const evs = [];
  for (const c of items) {
    if (c.notice_deadline && c.days_to_notice_deadline !== null && c.days_to_notice_deadline >= 0 && c.days_to_notice_deadline <= days)
      evs.push({ d: c.days_to_notice_deadline, kind: 'notice', label: 'Notice deadline', c });
    if (c.days_to_expiry !== null && c.days_to_expiry !== undefined && c.days_to_expiry >= 0 && c.days_to_expiry <= days)
      evs.push({ d: c.days_to_expiry, kind: c.auto_renew ? 'renew' : 'expiry', label: c.auto_renew ? 'Auto-renews' : 'Expires', c });
  }
  evs.sort((a, b) => a.d - b.d);
  if (!evs.length) return `<p class="muted" style="margin:0">No contract deadlines in the next ${days} days.</p>`;
  return `<div class="bz-tl"><div class="bz-tl-axis"><span>Today</span><span>${days / 3} days</span><span>${(2 * days) / 3} days</span><span>${days} days</span></div>
    <div class="bz-tl-line">${evs.map((e, i) => `<div class="bz-tl-ev ${e.kind} ${i % 2 ? 'low' : ''}" style="left:${(e.d / days * 100).toFixed(2)}%">
      <span class="pin"></span><div class="lbl"><b>${esc(e.c.title.split(' — ')[0])}</b><span>${esc(e.label)} · ${e.d === 0 ? 'today' : `in ${e.d} days`}</span></div></div>`).join('')}</div></div>`;
}

function contracts(c, live = {}) {
  const next = c.items.filter(x => x.days_to_notice_deadline !== null && x.days_to_notice_deadline >= 0).sort((a, b) => a.days_to_notice_deadline - b.days_to_notice_deadline)[0];
  return `<section class="bz-card">${head('contracts', c.title, 'Next 90 days', `<a class="small" style="margin-left:auto" href="#/contracts">Open contracts</a>`)}
    ${contractTimeline(c.items)}
    ${next ? `<div class="bz-verdict warn">${icon('bell')}<div><b>Decide on ${esc(next.title)} by ${fmt.date(next.notice_deadline)}</b>
      <span>It renews automatically after that${next.payment_terms ? `. Payment terms: ${esc(next.payment_terms)}` : ''}.</span></div></div>` : ''}
    ${contractDecisions(c.items, live)}</section>`;
}

// Smart options per contract that needs a decision: extend, end at expiry, or renegotiate first.
export function contractDecisions(items, live = {}) {
  const rows = items.map(x => {
    const lc = (live.contracts || {})[x.id] || x;
    const opts = lc.suggestions || [];
    if (!opts.length) return '';
    const pend = opts.find(o => o.pending);
    const ap = pend && pend.approval_id ? (live.approvals || {})[pend.approval_id] : null;
    const when = lc.notice_deadline && lc.days_to_notice_deadline >= 0 ? `decide by ${fmt.date(lc.notice_deadline)}` : `ends ${fmt.date(lc.end_date)}`;
    let body;
    if (pend) {
      body = `<div class="bz-dpend">${icon('approvals')}<span><b>${esc(pend.label)}</b> is waiting for approval.</span>
        ${ap && ap.status === 'PENDING' && ap.can_decide !== false ? `<button class="btn sm" data-reject="${ap.id}">Reject</button><button class="btn sm primary" data-approve="${ap.id}">Approve</button>`
          : `<a class="btn sm" href="#/approvals">Open To approve</a>`}</div>`;
    } else if (lc.status === 'ENDING') {
      body = `<div class="bz-dpend">${icon('check')}<span>Set to end on ${fmt.date(lc.end_date)}. Auto-renewal is off.</span></div>`;
    } else {
      body = `<div class="bz-opts">${opts.map(o => o.action === 'RENEGOTIATE'
        ? `<button type="button" class="bz-opt" data-quick="${esc(`Help me renegotiate ${lc.title} with ${lc.counterparty || 'the counterparty'}: summarise the current terms, what we spend with them, and three points I could push for before ${lc.notice_deadline || lc.end_date}.`)}"><b>${esc(o.label)}</b><small>${esc(o.detail)}</small></button>`
        : `<button type="button" class="bz-opt${o.recommended ? ' rec' : ''}" ${live.canDecideContracts ? '' : 'disabled title="Only an owner or manager can request this"'} data-contract-action="${lc.id}|${o.action}|${o.months || ''}">
            ${o.recommended ? '<span class="tag">Suggested</span>' : ''}<b>${esc(o.label)}</b><small>${esc(o.detail)}</small></button>`).join('')}</div>`;
    }
    return `<div class="bz-decide"><div class="bz-dt"><b>${esc(lc.title)}</b><span>${esc(lc.counterparty || '')}${lc.counterparty ? ' · ' : ''}${when}${lc.auto_renew ? ' · auto-renews' : ''}</span></div>${body}</div>`;
  }).filter(Boolean);
  if (!rows.length) return '';
  return `<div class="bz-decisions"><div class="bz-dh">What would you like to do?</div>${rows.join('')}
    <p class="bz-dnote">Extending or ending a contract goes to To approve first. Nothing changes until you approve.</p></div>`;
}

function priorities(c) {
  return `<section class="bz-card">${head('insights', c.title)}<ol class="bz-pri">${c.items.map(p => `<li><span class="badge ${p.priority === 'HIGH' ? 'red' : p.priority === 'MEDIUM' ? 'amber' : 'blue'}">${p.priority === 'HIGH' ? 'High' : p.priority === 'MEDIUM' ? 'Medium' : 'Low'}</span>
    <div><b>${esc(p.title)}</b><span>${esc(p.action || '')}</span></div></li>`).join('')}</ol></section>`;
}

// Purchase orders and payroll drafted by agents, with approve buttons and an approve-all action.
export function ordersCard(actions, approvalsById) {
  if (!actions.length) return '';
  const rows = actions.map(a => {
    const ap = a.approval_id ? approvalsById[a.approval_id] : null;
    const status = ap ? ap.status : a.status;
    const canDecide = ap && ap.status === 'PENDING' && ap.can_decide !== false;
    const done = status === 'APPROVED';
    a.status = status;
    return { a, ap, canDecide, html: `<div class="bz-order ${done ? 'done' : ''}">
      <span class="bz-hex sm">${icon(a.type === 'PAYROLL' ? 'payroll' : a.type === 'CONTRACT_ACTION' ? 'contracts' : 'procurement')}</span>
      <div class="bz-om"><b>${esc(a.title)}</b><span>${esc(a.detail || '')}</span></div>
      ${a.amount ? `<b class="num bz-oamt">${fmt.money(a.amount)}</b>` : ''}
      <div class="bz-oact">${canDecide ? `<button class="btn sm" data-reject="${a.approval_id}">Reject</button><button class="btn sm primary" data-approve="${a.approval_id}">Approve</button>`
        : ap && ap.status === 'PENDING' ? `<span class="badge amber">Needs ${esc(ap.required_role.toLowerCase())}</span>`
          : done ? `<span class="badge green">${icon('check')}${a.type === 'PURCHASE_ORDER' ? 'Sent to supplier' : 'Approved'}</span>` : statusBadge(status)}</div></div>` };
  });
  const pending = rows.filter(r => r.canDecide).map(r => r.a.approval_id);
  const total = actions.reduce((s, a) => s + (a.amount || 0), 0);
  const allDone = rows.every(r => r.a.status === 'APPROVED' || (r.ap && r.ap.status === 'APPROVED'));
  const allPO = actions.every(a => a.type === 'PURCHASE_ORDER');
  return `<section class="bz-card orders">${head('approvals', allDone ? (allPO ? 'Approved and sent' : 'Approved') : 'Ready for your approval',
      allDone ? (allPO ? 'Orders were submitted to suppliers (simulated in this version).' : 'Done. The changes are saved.') : 'Prepared by your agents. Nothing is sent, paid or changed until you approve.',
      pending.length > 1 ? `<button class="btn primary sm" style="margin-left:auto" data-approve-all="${pending.join(',')}">${icon('check')} Approve all · ${fmt.money(total, { dp: 0 })}</button>` : '')}
    <div class="bz-orders">${rows.map(r => r.html).join('')}</div></section>`;
}

const RENDER = { pulse, stock, runway, payroll, contracts, priorities };
export function renderCards(cards, live = {}) {
  return (cards || []).filter(c => RENDER[c.type]).map(c => RENDER[c.type](c, live)).join('');
}
