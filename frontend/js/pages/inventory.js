import { api } from '../api.js';
import { refreshCounts } from '../app.js';
import { barChart } from '../charts.js';
import { can, confirmBox, empty, errToast, esc, field, fmt, icon, meter, modal, on, skeleton, statusBadge, toast } from '../ui.js';

export async function render(el) {
  el.innerHTML = `<div class="page-head"><div><h1>Inventory</h1><div class="lead" id="invLead">Stock levels, forecasts and restocking.</div></div>
    <div class="actions">
      <a class="btn" href="#/assistant?q=${encodeURIComponent('Which products are running low, when will they run out, and what should I order?')}">${icon('sparkle')} Ask about stock</a>
      ${can('procurement.write') ? `<button class="btn primary" id="restockBtn">${icon('procurement')} Plan restock</button>` : ''}
      ${can('inventory.write') ? `<button class="btn" id="addBtn">${icon('plus')} Add product</button>` : ''}
    </div></div>
    <section class="panel"><div class="panel-body table-wrap" id="tbl">${skeleton(6)}</div></section>
    <p class="small muted" style="margin-top:10px">Stock percentage = current stock ÷ maximum capacity × 100. Forecasts use a moving average of recorded usage, so every number can be traced.</p>`;
  let data, suppliers = [];

  async function load() {
    try { data = await api.get('/api/inventory'); } catch (e) { return errToast(e); }
    const th = data.thresholds;
    el.querySelector('#invLead').textContent = `Alert below ${th.low_stock_pct}% of capacity, urgent below ${th.urgent_stock_pct}%, critical below ${th.critical_stock_pct}%. Stock on hand is worth ${fmt.money(data.inventory_value)} at cost.`;
    if (!data.products.length) {
      el.querySelector('#tbl').innerHTML = empty('No products yet', 'Add the items you stock so Beezy can watch levels and forecast stockouts.');
      return;
    }
    el.querySelector('#tbl').innerHTML = `<table class="t"><thead><tr><th>Product</th><th>SKU</th><th>Category</th><th class="right">Stock</th><th class="right">Capacity</th>
      <th style="min-width:130px">Stock level</th><th class="right">Reorder at</th><th class="right">Days of cover</th><th>Supplier</th><th class="right">Unit cost</th><th>Status</th><th></th></tr></thead>
      <tbody>${data.products.map(p => `<tr>
        <td class="strong" style="white-space:nowrap"><a href="#" data-detail="${p.id}">${esc(p.name)}</a><div class="small muted">${esc(p.unit)}</div></td>
        <td class="muted small" style="white-space:nowrap">${esc(p.sku)}</td><td>${esc(p.category)}</td>
        <td class="right num">${fmt.num(p.current_stock, 1)}</td><td class="right num muted">${fmt.num(p.max_stock)}</td>
        <td><div class="row" style="flex-wrap:nowrap">${meter(p.stock_pct, p.level, th.low_stock_pct)}<span class="num small" style="width:44px;text-align:right">${p.stock_pct}%</span></div></td>
        <td class="right num muted">${fmt.num(p.reorder_threshold_qty, 1)}</td>
        <td class="right num" title="${p.depletion_date ? 'Projected stockout ' + fmt.date(p.depletion_date) : ''}">${p.days_of_cover ?? '—'}${p.stockout_risk === 'HIGH' ? ' <span class="badge red">risk</span>' : ''}</td>
        <td class="small">${esc(p.supplier_name || '—')}</td><td class="right num">${fmt.money(p.unit_cost)}</td>
        <td>${statusBadge(p.level)}</td>
        <td class="actions">${can('inventory.write') ? `<button class="btn sm" data-adjust="${p.id}">Adjust</button> <button class="btn sm ghost" data-edit="${p.id}">Edit</button>` : ''}</td>
      </tr>`).join('')}</tbody></table>`;
  }

  async function ensureSuppliers() {
    if (!suppliers.length && can('procurement.read')) { try { suppliers = (await api.get('/api/suppliers')).suppliers; } catch (e) { suppliers = []; } }
  }

  on(el, 'click', '[data-adjust]', async (e, b) => {
    const p = data.products.find(x => x.id === Number(b.dataset.adjust));
    const r = await modal({ title: `Adjust stock: ${p.name}`, body: `<p class="muted">On hand: ${fmt.num(p.current_stock, 1)} ${esc(p.unit)} (${p.stock_pct}%).</p><div class="form-grid">
      ${field('kind', 'Type', { options: [['sale', 'Sale / usage (removes stock)'], ['restock', 'Delivery received (adds stock)'], ['waste', 'Waste / spoilage (removes)'], ['adjustment', 'Stock count correction (+/−)']] })}
      ${field('qty', 'Quantity', { type: 'number', step: '0.1', required: true })}${field('note', 'Note', { full: true })}</div>`, submit: 'Record' });
    if (!r) return;
    let q = Number(r.qty);
    if (!q) return toast('Enter a quantity', 'error');
    if (r.kind === 'sale' || r.kind === 'waste') q = -Math.abs(q);
    if (r.kind === 'restock') q = Math.abs(q);
    try {
      const res = await api.post(`/api/inventory/${p.id}/adjust`, { delta: q, kind: r.kind, note: r.note });
      toast(`${p.name}: now ${fmt.num(res.product.current_stock, 1)} (${res.product.stock_pct}%)`, 'ok');
      for (const a of res.automations || []) toast(`Automation "${a.rule}": ${a.summary}`, 'ok');
      if ((res.automations || []).length) refreshCounts();
      load();
    } catch (err) { errToast(err); }
  });

  const productForm = (p = {}) => `<div class="form-grid">
    ${field('name', 'Name', { value: p.name || '', required: true })}${p.id ? '' : field('sku', 'SKU', { required: true })}
    ${field('category', 'Category', { value: p.category || 'General' })}${field('unit', 'Unit', { value: p.unit || 'unit', hint: 'e.g. bag (1kg), carton, bottle' })}
    ${p.id ? '' : field('current_stock', 'Current stock', { type: 'number', step: '0.1', value: 0 })}
    ${field('max_stock', 'Maximum capacity', { type: 'number', step: '0.1', value: p.max_stock || '', required: true, hint: 'Alerts fire as a % of this' })}
    ${field('unit_cost', 'Unit cost', { type: 'number', step: '0.01', value: p.unit_cost ?? 0 })}${field('unit_price', 'Selling price (optional)', { type: 'number', step: '0.01', value: p.unit_price ?? 0 })}
    ${suppliers.length ? field('preferred_supplier_id', 'Preferred supplier', { options: [['', 'None'], ...suppliers.map(s => [s.id, s.name])], value: p.preferred_supplier_id || '', full: true }) : ''}</div>`;
  const numify = (r) => { for (const k of ['current_stock', 'max_stock', 'unit_cost', 'unit_price']) if (k in r) r[k] = Number(r[k] || 0); r.preferred_supplier_id = r.preferred_supplier_id ? Number(r.preferred_supplier_id) : null; return r; };

  el.querySelector('#addBtn')?.addEventListener('click', async () => {
    await ensureSuppliers();
    const r = await modal({ title: 'Add product', body: productForm(), submit: 'Add product' });
    if (!r) return;
    try { await api.post('/api/inventory', numify(r)); toast('Product added', 'ok'); load(); } catch (e) { errToast(e); }
  });
  on(el, 'click', '[data-edit]', async (e, b) => {
    await ensureSuppliers();
    const p = data.products.find(x => x.id === Number(b.dataset.edit));
    const r = await modal({ title: `Edit ${p.name}`, body: productForm(p) + `<div style="margin-top:14px"><button type="button" class="btn sm danger" id="archive">Archive product</button></div>`, submit: 'Save changes',
      onOpen: (form, close) => form.querySelector('#archive').onclick = async () => {
        close(null);
        if (await confirmBox('Archive product', `Archive ${p.name}? History is kept, but it will no longer be tracked.`, 'Archive', true)) {
          try { await api.del(`/api/inventory/${p.id}`); toast('Archived'); load(); } catch (err) { errToast(err); }
        }
      } });
    if (!r) return;
    try { await api.put(`/api/inventory/${p.id}`, numify(r)); toast('Saved', 'ok'); load(); } catch (e) { errToast(e); }
  });

  on(el, 'click', '[data-detail]', async (e, a) => {
    e.preventDefault();
    const id = Number(a.dataset.detail);
    const p = data.products.find(x => x.id === id);
    const back = modal({ title: p.name, body: `<div id="pd">${skeleton(6)}</div>`, wide: true, noFooter: true });
    try {
      const [f, h, q] = await Promise.all([api.get(`/api/inventory/${id}/forecast`), api.get(`/api/inventory/${id}/history`),
        can('procurement.read') ? api.get(`/api/inventory/${id}/quotes`) : Promise.resolve(null)]);
      const fc = f.forecast;
      document.getElementById('pd').innerHTML = `<div class="grid g2">
        <div><h3 style="margin-bottom:8px">Forecast</h3><dl class="kv">
          <dt>Method</dt><dd>${esc(fc.method)}</dd><dt>Avg daily usage</dt><dd>${fc.avg_daily_sales} ${esc(fc.unit)}${fc.demand_trend_pct !== null ? ` <span class="${fc.demand_trend_pct >= 0 ? 'up' : 'down'}">(${fmt.pct(fc.demand_trend_pct, true)} vs previous ${fc.window_days} days)</span>` : ''}</dd>
          <dt>Weekly demand</dt><dd>${fc.weekly_demand}</dd><dt>Days of cover</dt><dd>${fc.days_of_cover ?? '—'}</dd>
          <dt>Projected stockout</dt><dd>${fmt.date(fc.depletion_date)}</dd><dt>Supplier lead time</dt><dd>${fc.supplier_lead_time_days ?? '—'} days</dd>
          <dt>Stockout risk</dt><dd>${statusBadge(fc.stockout_risk === 'NONE' ? 'HEALTHY' : fc.stockout_risk)}</dd><dt>Refill to capacity</dt><dd>${fc.quantity_to_refill} ${esc(fc.unit)}</dd></dl></div>
        <div><h3 style="margin-bottom:8px">Usage, last 30 days</h3>${barChart({ labels: f.daily_sales.map(d => d.date.slice(8)), series: [{ name: 'Units', values: f.daily_sales.map(d => d.units) }], height: 170, format: v => fmt.num(v), tipFormat: v => fmt.num(v, 1) })}</div></div>
        ${q && q.quotes.length ? `<h3 style="margin:16px 0 8px">Supplier comparison</h3><p class="small">${esc(q.rationale)}</p><div class="table-wrap"><table class="t"><thead><tr><th>Supplier</th><th class="right">Unit price</th><th class="right">MOQ</th><th class="right">Lead time</th><th>Arrives</th><th class="right">Order qty</th><th class="right">Total</th><th></th></tr></thead><tbody>
          ${q.quotes.map(x => `<tr><td class="strong">${esc(x.supplier)}${x.is_preferred ? ' <span class="badge outline">preferred</span>' : ''}</td><td class="right num">${fmt.money(x.unit_price)}${x.price_change_pct ? ` <span class="small down">${fmt.pct(x.price_change_pct, true)}</span>` : ''}</td>
          <td class="right num">${fmt.num(x.min_order_qty)}</td><td class="right num">${x.lead_time_days} d</td><td>${x.arrives_before_stockout ? '<span class="badge green">before stockout</span>' : '<span class="badge red">too late</span>'}</td>
          <td class="right num">${fmt.num(x.order_quantity)}</td><td class="right num">${fmt.money(x.total_cost)}</td><td>${x.recommended ? '<span class="badge teal">recommended</span>' : ''}</td></tr>`).join('')}</tbody></table></div>` : ''}
        <h3 style="margin:16px 0 8px">Recent movements</h3><div class="table-wrap" style="max-height:220px;overflow:auto"><table class="t"><tbody>${h.movements.slice(0, 25).map(m => `<tr><td>${fmt.date(m.occurred_on)}</td><td>${esc(fmt.status(m.kind))}</td><td class="right num ${m.quantity < 0 ? 'down' : 'up'}">${m.quantity > 0 ? '+' : ''}${fmt.num(m.quantity, 1)}</td><td class="small muted">${esc(m.note || '')}</td></tr>`).join('')}</tbody></table></div>
        <div class="row" style="margin-top:14px"><a class="btn" href="#/assistant?q=${encodeURIComponent(`Should I restock ${p.name} now, from which supplier, and how much?`)}">${icon('sparkle')} Ask Beezy about ${esc(p.name)}</a></div>`;
    } catch (err) { document.getElementById('pd').innerHTML = `<p class="muted">${esc(err.message)}</p>`; }
    await back;
  });

  el.querySelector('#restockBtn')?.addEventListener('click', async () => {
    let plan;
    try { plan = await api.get('/api/procurement/restock-plan'); } catch (e) { return errToast(e); }
    const lines = plan.items;
    if (!lines.length) return toast('Every product is above the threshold. Nothing to restock.');
    const r = await modal({ title: 'Restock plan', wide: true, submit: 'Draft purchase orders', body: `<p class="muted">${esc(plan.note)}</p>
      <div class="table-wrap"><table class="t"><thead><tr><th></th><th>Product</th><th class="right">Stock</th><th class="right">Cover</th><th>Supplier</th><th class="right">Qty</th><th class="right">Cost</th><th>Why</th></tr></thead><tbody>
      ${lines.map(l => `<tr><td><input type="checkbox" name="p_${l.product_id}" ${l.already_on_order || !l.supplier ? 'disabled' : 'checked'} aria-label="Include ${esc(l.product)}"></td>
        <td class="strong">${esc(l.product)}${l.already_on_order ? `<div class="small muted">On order: ${esc(l.already_on_order)}</div>` : ''}</td><td class="right num">${l.stock_pct}%</td><td class="right num">${l.days_of_cover ?? '—'} d</td>
        <td>${esc(l.supplier || '—')}</td><td class="right num">${fmt.num(l.quantity)}</td><td class="right num">${fmt.money(l.line_total)}</td><td class="small muted" style="max-width:320px">${esc(l.rationale || '')}</td></tr>`).join('')}</tbody></table></div>
      <p class="strong" style="margin-top:10px">Estimated total for new orders: ${fmt.money(plan.estimated_total)}</p>
      <p class="small muted">Orders are created as drafts awaiting approval. Nothing is sent to suppliers until approved.</p>` });
    if (!r) return;
    const ids = Object.entries(r).filter(([k, v]) => k.startsWith('p_') && v).map(([k]) => Number(k.slice(2)));
    if (!ids.length) return;
    try {
      const res = await api.post('/api/procurement/draft-restock', { product_ids: ids });
      toast(`${res.created.length} purchase order(s) drafted for approval (${fmt.money(res.total)})`, 'ok');
      refreshCounts();
      location.hash = '#/procurement';
    } catch (e) { errToast(e); }
  });

  await load();
}
