import { api } from '../api.js';
import { refreshCounts } from '../app.js';
import { can, confirmBox, errToast, esc, field, fmt, icon, modal, on, skeleton, statusBadge, toast } from '../ui.js';

export async function render(el, params) {
  let tab = params.get('tab') || 'orders';
  el.innerHTML = `<div class="page-head"><div><h1>Suppliers &amp; procurement</h1><div class="lead">Purchase orders move from draft to approval to supplier. Supplier submission is simulated in this version; no real orders or payments are sent.</div></div>
    <div class="actions"><a class="btn" href="#/assistant?q=${encodeURIComponent('Find the best supplier for each low-stock product, considering price, delivery time and our stockout dates, and prepare the purchase orders.')}">${icon('sparkle')} Ask the Procurement Agent</a>
    ${can('procurement.write') ? `<button class="btn primary" id="newPo">${icon('plus')} New purchase order</button>` : ''}</div></div>
    <div class="tabs">${[['orders', 'Purchase orders'], ['suppliers', 'Suppliers']].map(([k, l]) => `<button data-tab="${k}" class="${tab === k ? 'on' : ''}">${l}</button>`).join('')}</div><div id="pane">${skeleton(5)}</div>`;
  const pane = el.querySelector('#pane');
  on(el, 'click', '[data-tab]', (e, b) => { tab = b.dataset.tab; el.querySelectorAll('[data-tab]').forEach(x => x.classList.toggle('on', x === b)); show(); });
  let sups = null;
  const loadSups = async () => { if (!sups) sups = (await api.get('/api/suppliers')); return sups; };

  let poData = { purchase_orders: [] };
  async function orders() {
    const r = poData = await api.get('/api/purchase-orders');
    const groups = [['Needs approval', ['AWAITING_APPROVAL', 'DRAFT']], ['With supplier', ['APPROVED', 'SUBMITTED', 'PARTIALLY_RECEIVED']], ['Closed', ['COMPLETED', 'CANCELLED']]];
    pane.innerHTML = groups.map(([title, st]) => {
      const rows = r.purchase_orders.filter(p => st.includes(p.status));
      return `<section class="panel" style="margin-bottom:16px"><div class="panel-head"><h2>${title}</h2><span class="sub">${rows.length}</span></div><div class="panel-body table-wrap">
        ${rows.length ? `<table class="t"><thead><tr><th>PO</th><th>Supplier</th><th>Items</th><th>Created by</th><th>Expected</th><th class="right">Total</th><th>Status</th><th></th></tr></thead><tbody>
        ${rows.map(p => `<tr><td class="strong">${esc(p.po_number)}<div class="small muted">${fmt.date(p.created_at)}</div></td><td>${esc(p.supplier)}</td>
          <td class="small">${p.items.map(i => `${fmt.num(i.quantity)} × ${esc(i.product)}`).join('<br>')}</td>
          <td class="small">${esc(p.created_by)}${p.origin !== 'manual' ? ` <span class="badge teal">${esc(p.origin)}</span>` : ''}</td>
          <td>${fmt.date(p.expected_date)}${p.status === 'SUBMITTED' && p.expected_date < new Date().toISOString().slice(0, 10) ? ' <span class="badge red">late</span>' : ''}</td>
          <td class="right num strong">${fmt.money(p.total)}</td><td>${statusBadge(p.status)}</td>
          <td class="actions">${p.status === 'AWAITING_APPROVAL' && can('procurement.approve') ? `<button class="btn sm primary" data-approve-po="${p.id}">Approve</button> ` : ''}
            ${['SUBMITTED', 'PARTIALLY_RECEIVED'].includes(p.status) && can('procurement.write') ? `<button class="btn sm teal" data-receive="${p.id}">Receive</button> ` : ''}
            ${['DRAFT', 'AWAITING_APPROVAL', 'APPROVED', 'SUBMITTED'].includes(p.status) && can('procurement.write') ? `<button class="btn sm ghost" data-cancel="${p.id}">Cancel</button>` : ''}
            <button class="btn sm ghost" data-view="${p.id}">View</button></td></tr>`).join('')}</tbody></table>` : '<p class="muted" style="margin:0">None.</p>'}</div></section>`;
    }).join('');
  }

  on(pane, 'click', '[data-view]', async (e, b) => {
    const p = poData.purchase_orders.find(x => x.id === Number(b.dataset.view));
    await modal({ title: p.po_number, noFooter: true, body: `<dl class="kv"><dt>Supplier</dt><dd>${esc(p.supplier)}</dd><dt>Status</dt><dd>${statusBadge(p.status)}</dd><dt>Created by</dt><dd>${esc(p.created_by)}</dd>
      <dt>Approved by</dt><dd>${esc(p.approved_by || '—')}</dd><dt>Expected</dt><dd>${fmt.date(p.expected_date)}</dd></dl>
      <table class="t" style="margin-top:12px"><thead><tr><th>Item</th><th class="right">Qty</th><th class="right">Received</th><th class="right">Unit price</th><th class="right">Line total</th></tr></thead><tbody>
      ${p.items.map(i => `<tr><td>${esc(i.product)}</td><td class="right num">${fmt.num(i.quantity)}</td><td class="right num">${fmt.num(i.received_qty)}</td><td class="right num">${fmt.money(i.unit_price)}</td><td class="right num">${fmt.money(i.line_total)}</td></tr>`).join('')}
      <tr><td colspan="4" class="strong">Total</td><td class="right num strong">${fmt.money(p.total)}</td></tr></tbody></table>${p.notes ? `<p class="small muted" style="margin-top:10px">${esc(p.notes)}</p>` : ''}` });
  });
  on(pane, 'click', '[data-approve-po]', async (e, b) => {
    b.disabled = true;
    try { await api.post(`/api/purchase-orders/${b.dataset.approvePo}/approve`); toast('Approved and submitted to supplier (simulated)', 'ok'); refreshCounts(); show(); } catch (err) { errToast(err); b.disabled = false; }
  });
  on(pane, 'click', '[data-receive]', async (e, b) => {
    if (!await confirmBox('Receive goods', 'Mark all remaining items as delivered? Stock levels increase and a supplier bill is created.', 'Receive all')) return;
    try { await api.post(`/api/purchase-orders/${b.dataset.receive}/receive`, {}); toast('Goods received — stock and payables updated', 'ok'); show(); } catch (err) { errToast(err); }
  });
  on(pane, 'click', '[data-cancel]', async (e, b) => {
    if (!await confirmBox('Cancel order', 'Cancel this purchase order?', 'Cancel order', true)) return;
    try { await api.post(`/api/purchase-orders/${b.dataset.cancel}/cancel`, { reason: 'Cancelled by user' }); toast('Cancelled'); refreshCounts(); show(); } catch (err) { errToast(err); }
  });

  async function suppliers() {
    sups = null;
    const r = await loadSups();
    pane.innerHTML = `${can('procurement.write') ? `<div class="row" style="margin-bottom:12px"><button class="btn" id="addSup">${icon('plus')} Add supplier</button></div>` : ''}
      ${r.price_changes.length ? `<section class="panel" style="margin-bottom:16px"><div class="panel-head"><h2>Recent price changes</h2></div><div class="panel-body">${r.price_changes.map(c => `<div class="alert-row"><span class="sev ${c.change_pct > 0 ? 'warning' : 'info'}"></span><div><div class="a-title">${esc(c.supplier)}: ${esc(c.product)} ${fmt.pct(c.change_pct, true)}</div><div class="a-detail">${fmt.money(c.previous_unit_price)} → ${fmt.money(c.unit_price)}</div></div></div>`).join('')}</div></section>` : ''}
      <div class="grid g2">${r.suppliers.map(s => `<section class="panel"><div class="panel-head"><h2>${esc(s.name)}</h2>${s.is_preferred ? '<span class="badge teal">Preferred</span>' : ''}
        <div class="tools">${can('procurement.write') ? `<button class="btn sm ghost" data-price="${s.id}">Add / update price</button>` : ''}</div></div>
        <div class="panel-body"><dl class="kv"><dt>Contact</dt><dd>${esc(s.contact_name || '—')} · ${esc(s.email || '')} ${esc(s.phone || '')}</dd><dt>Payment terms</dt><dd>${esc(s.payment_terms)}</dd>
          <dt>Reliability</dt><dd>${s.reliability.toFixed(1)} / 5</dd><dt>On-time delivery</dt><dd>${s.performance.on_time_rate_pct !== null ? s.performance.on_time_rate_pct + '% of ' + s.performance.completed_orders + ' orders' : 'No completed orders yet'}</dd>
          <dt>Total spend</dt><dd>${fmt.money(s.performance.total_spend)}</dd></dl>
        <table class="t" style="margin-top:10px"><thead><tr><th>Product</th><th class="right">Price</th><th class="right">MOQ</th><th class="right">Lead time</th></tr></thead><tbody>
          ${s.products.map(p => `<tr><td>${esc(p.product)}</td><td class="right num">${fmt.money(p.unit_price)}</td><td class="right num">${fmt.num(p.min_order_qty)}</td><td class="right num">${p.lead_time_days} d</td></tr>`).join('') || '<tr><td colspan="4" class="muted">No products yet</td></tr>'}</tbody></table></div></section>`).join('')}</div>`;
    pane.querySelector('#addSup')?.addEventListener('click', async () => {
      const f = await modal({ title: 'Add supplier', body: `<div class="form-grid">${field('name', 'Company name', { required: true, full: true })}${field('contact_name', 'Contact')}${field('email', 'Email', { type: 'email' })}
        ${field('phone', 'Phone')}${field('payment_terms', 'Payment terms', { value: 'Net 30' })}${field('reliability', 'Reliability (0–5)', { type: 'number', step: '0.1', value: 4 })}${field('is_preferred', 'Preferred supplier', { type: 'checkbox' })}</div>` });
      if (!f) return;
      try { await api.post('/api/suppliers', { ...f, reliability: Number(f.reliability) }); toast('Supplier added', 'ok'); suppliers(); } catch (err) { errToast(err); }
    });
  }
  on(pane, 'click', '[data-price]', async (e, b) => {
    const inv = await api.get('/api/inventory');
    const f = await modal({ title: 'Supplier price', body: `<div class="form-grid">${field('product_id', 'Product', { options: inv.products.map(p => [p.id, p.name]), full: true })}
      ${field('unit_price', 'Unit price', { type: 'number', step: '0.01', required: true })}${field('min_order_qty', 'Minimum order qty', { type: 'number', value: 1 })}${field('lead_time_days', 'Lead time (days)', { type: 'number', value: 3 })}</div>` });
    if (!f) return;
    try { await api.post(`/api/suppliers/${b.dataset.price}/products`, { product_id: Number(f.product_id), unit_price: Number(f.unit_price), min_order_qty: Number(f.min_order_qty), lead_time_days: Number(f.lead_time_days) }); toast('Price saved', 'ok'); suppliers(); } catch (err) { errToast(err); }
  });

  el.querySelector('#newPo')?.addEventListener('click', async () => {
    const r = await loadSups();
    const opts = r.suppliers.filter(s => s.products.length);
    const f = await modal({ title: 'New purchase order', body: `<div class="form-grid">${field('supplier_id', 'Supplier', { options: opts.map(s => [s.id, s.name]), full: true })}
      <div class="field full"><label for="f_product_id">Product</label><select class="input" name="product_id" id="f_product_id"></select><span class="hint" id="poHint"></span></div>
      ${field('quantity', 'Quantity', { type: 'number', step: '1', required: true })}${field('notes', 'Notes', { full: true })}</div>
      <p class="small muted">The order goes to the approval queue before it is sent.</p>`, submit: 'Create order',
      onOpen: (form) => {
        const sel = form.querySelector('[name=supplier_id]'), prod = form.querySelector('[name=product_id]'), hint = form.querySelector('#poHint');
        const fill = () => { const s = opts.find(x => x.id === Number(sel.value)); prod.innerHTML = s.products.map(p => `<option value="${p.product_id}" data-h="${esc(`${fmt.money(p.unit_price)} each, MOQ ${p.min_order_qty}, ${p.lead_time_days}-day lead time`)}">${esc(p.product)}</option>`).join(''); upd(); };
        const upd = () => { hint.textContent = prod.selectedOptions[0]?.dataset.h || ''; };
        sel.onchange = fill; prod.onchange = upd; fill();
      } });
    if (!f) return;
    try {
      const po = await api.post('/api/purchase-orders', { supplier_id: Number(f.supplier_id), items: [{ product_id: Number(f.product_id), quantity: Number(f.quantity) }], notes: f.notes || null });
      toast(`${po.po_number} created — ${fmt.status(po.status).toLowerCase()}`, 'ok'); refreshCounts(); tab = 'orders'; show();
    } catch (err) { errToast(err); }
  });

  async function show() { try { tab === 'suppliers' ? await suppliers() : await orders(); } catch (e) { pane.innerHTML = `<p class="muted">${esc(e.message)}</p>`; } }
  show();
}
