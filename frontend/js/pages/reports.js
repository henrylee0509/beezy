import { api, download } from '../api.js';
import { aiSource, can, errToast, esc, field, fmt, icon, md, modal, on, skeleton, toast } from '../ui.js';

const PERMS = { FINANCIAL: 'finance.read', INVENTORY: 'inventory.read', PAYROLL: 'salary.read', SUPPLIER: 'procurement.read', PERFORMANCE: 'finance.read' };

export async function render(el) {
  el.innerHTML = `<div class="page-head"><div><h1>Reports</h1><div class="lead">Generate reports from live data, optionally with a management summary written by Muse Spark, and export to PDF or CSV.</div></div>
    <div class="actions"><button class="btn primary" id="gen">${icon('plus')} Generate report</button></div></div>
    <div class="grid g-2-1"><section class="panel"><div class="panel-body" id="view"><div class="empty"><h3>Pick or generate a report</h3><p>Reports are saved here so you can export them again later.</p></div></div></section>
    <section class="panel"><div class="panel-head"><h2>Saved reports</h2></div><div class="panel-body" id="list">${skeleton(4)}</div></section></div>`;
  let kinds = {};
  async function list() {
    const r = await api.get('/api/reports');
    kinds = r.kinds;
    el.querySelector('#list').innerHTML = r.reports.length ? r.reports.map(x => `<a href="#" class="alert-row" data-open="${x.id}"><span class="sev info"></span><div><div class="a-title">${esc(x.title)}</div><div class="a-detail">${fmt.datetime(x.created_at)} · ${esc(x.created_by || '')}</div></div></a>`).join('') : '<p class="muted">No reports yet.</p>';
  }
  async function open(id) {
    const v = el.querySelector('#view');
    v.innerHTML = skeleton(6);
    try {
      const r = await api.get(`/api/reports/${id}`);
      const t = r.data.table || {};
      v.innerHTML = `<div class="row"><h2>${esc(r.title)}</h2><span style="margin-left:auto" class="row"><button class="btn sm" data-dl="pdf">${icon('download')} PDF</button><button class="btn sm" data-dl="csv">${icon('download')} CSV</button></span></div>
        <div class="kpis" style="margin:14px 0">${Object.entries(r.data.kpis || {}).map(([k, val]) => `<div class="kpi"><div class="k-label">${esc(k.replace(/_/g, ' ').replace(/^\w/, c => c.toUpperCase()))}</div><div class="k-value num" style="font-size:17px">${typeof val === 'number' && !Number.isInteger(val) ? fmt.money(val) : esc(val)}</div></div>`).join('')}</div>
        ${r.narrative ? `<h3 style="margin-bottom:6px">Management summary</h3><div class="ai-prose" style="font-size:16px">${md(r.narrative)}</div>${aiSource(r.narrative_source)}` : ''}
        <div class="table-wrap" style="margin-top:14px"><table class="t"><thead><tr>${(t.columns || []).map(c => `<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>${(t.rows || []).map(row => `<tr>${row.map(c => `<td class="${typeof c === 'number' ? 'right num' : ''}">${typeof c === 'number' ? (Number.isInteger(c) ? c : fmt.money(c, { noCur: true })) : esc(c ?? '')}</td>`).join('')}</tr>`).join('')}</tbody></table></div>
        ${(r.data.recommendations || []).length ? `<h3 style="margin:14px 0 6px">Priorities</h3><ul>${r.data.recommendations.map(x => `<li><b>${esc(x.title)}</b> — ${esc(x.action)}</li>`).join('')}</ul>` : ''}
        ${(r.data.notes || []).length ? `<div class="small muted" style="margin-top:12px">${r.data.notes.map(esc).join('<br>')}</div>` : ''}`;
      on(v, 'click', '[data-dl]', (e, b) => download(`/api/reports/${id}/export?format=${b.dataset.dl}`, `report.${b.dataset.dl}`).catch(errToast));
    } catch (e) { v.innerHTML = `<p class="muted">${esc(e.message)}</p>`; }
  }
  on(el.querySelector('#list'), 'click', '[data-open]', (e, a) => { e.preventDefault(); const v = el.querySelector('#view'); const fresh = v.cloneNode(false); v.replaceWith(fresh); open(Number(a.dataset.open)); });
  el.querySelector('#gen').onclick = async () => {
    const options = Object.entries(kinds).filter(([k]) => can(PERMS[k]));
    const f = await modal({ title: 'Generate report', body: `<div class="form-grid">${field('kind', 'Report', { options, full: true })}${field('period', 'Month (YYYY-MM)', { value: new Date().toISOString().slice(0, 7) })}
      ${field('ai_summary', 'Add a management summary from Muse Spark', { type: 'checkbox', value: true, full: true })}</div>`, submit: 'Generate' });
    if (!f) return;
    toast('Generating report…');
    try { const r = await api.post('/api/reports/generate', { kind: f.kind, period: f.period || null, ai_summary: !!f.ai_summary }); await list(); const v = el.querySelector('#view'); const fresh = v.cloneNode(false); v.replaceWith(fresh); open(r.id); }
    catch (e) { errToast(e); }
  };
  list();
}
