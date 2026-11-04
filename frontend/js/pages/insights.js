import { api } from '../api.js';
import { aiSource, can, errToast, esc, fmt, icon, on, skeleton, statusBadge, toast } from '../ui.js';

const LINKS = { Inventory: '#/inventory', Finance: '#/finance', Payroll: '#/payroll', Contracts: '#/contracts', Procurement: '#/procurement' };

export async function render(el) {
  let filter = 'open';
  el.innerHTML = `<div class="page-head"><div><h1>Insights</h1><div class="lead">Each recommendation comes from a rule that watches your data, with the supporting numbers attached. Muse Spark can explain any of them in plain language; it cannot add figures of its own.</div></div>
    <div class="actions"><div class="seg"><button data-f="open" class="on">Open</button><button data-f="all">All</button></div><button class="btn" id="refresh">${icon('refresh')} Re-scan data</button></div></div>
    <div id="list">${skeleton(6)}</div>`;
  let data;
  async function load(refresh = false) {
    try { data = await api.get('/api/insights' + (refresh ? '?refresh=1' : '')); } catch (e) { return errToast(e); }
    const recs = data.recommendations.filter(r => filter === 'all' || ['PENDING', 'ACCEPTED'].includes(r.status));
    const byCat = {};
    recs.forEach(r => (byCat[r.category] = byCat[r.category] || []).push(r));
    el.querySelector('#list').innerHTML = recs.length ? data.categories.filter(c => byCat[c]).map(c => `<h2 style="margin:18px 0 10px">${esc(c)} <span class="muted small">${byCat[c].length}</span></h2>
      <div class="grid g2">${byCat[c].map(card).join('')}</div>`).join('') : `<div class="empty"><h3>No open recommendations</h3><p>Everything the detectors watch is within range.</p></div>`;
  }
  const card = (r) => {
    const ex = r.ai_explanation ? (typeof r.ai_explanation === 'string' ? JSON.parse(r.ai_explanation) : r.ai_explanation) : null;
    return `<article class="rec ${r.priority}" data-rec="${r.id}">
      <div class="row"><span class="r-title">${esc(r.title)}</span><span style="margin-left:auto" class="row"><span class="badge ${{ HIGH: 'red', MEDIUM: 'amber', LOW: 'blue' }[r.priority]}">${r.priority === 'HIGH' ? 'High' : r.priority === 'MEDIUM' ? 'Medium' : 'Low'} priority</span> ${r.status !== 'PENDING' ? statusBadge(r.status) : ''}</span></div>
      <div>${esc(r.description)}</div>
      <div class="small"><b>Suggested action:</b> ${esc(r.suggested_action || '')}</div>
      <div class="small muted">${esc(r.impact || '')}${r.estimated_benefit ? ` · potential ${fmt.money(r.estimated_benefit)}` : ''} · confidence ${esc(r.confidence.toLowerCase())} · ${esc(r.agent)}</div>
      <details><summary>Supporting data</summary><pre class="small" style="white-space:pre-wrap;margin:6px 0 0;font-family:inherit">${esc(JSON.stringify(r.supporting_data, null, 2))}</pre></details>
      <div class="ai-box">${ex ? `<div class="ai-prose" style="font-size:15.5px;margin-top:4px">${esc(ex.text)}</div>${aiSource(ex.source, ex.verification)}` : ''}</div>
      <div class="row" style="margin-top:4px">
        <button class="btn sm" data-explain="${r.id}">${icon('sparkle')} ${ex ? 'Explain again' : 'Explain'}</button>
        ${LINKS[r.module] ? `<a class="btn sm ghost" href="${LINKS[r.module]}">Open ${esc(r.module)}</a>` : ''}
        ${can('insights.write') ? `<span style="margin-left:auto" class="row">${['ACCEPTED', 'COMPLETED', 'REJECTED'].filter(s => s !== r.status).map(s => `<button class="btn sm ghost" data-status="${s}" data-id="${r.id}">${{ ACCEPTED: 'Accept', COMPLETED: 'Mark done', REJECTED: 'Dismiss' }[s]}</button>`).join('')}</span>` : ''}
      </div></article>`;
  };
  on(el, 'click', '[data-f]', (e, b) => { filter = b.dataset.f; el.querySelectorAll('[data-f]').forEach(x => x.classList.toggle('on', x === b)); load(); });
  el.querySelector('#refresh').onclick = async () => { try { const r = await api.post('/api/insights/refresh'); toast(`${r.detected} detected, ${r.new} new, ${r.auto_resolved} resolved`, 'ok'); load(); } catch (e) { errToast(e); } };
  on(el, 'click', '[data-explain]', async (e, b) => {
    b.disabled = true;
    const box = b.closest('.rec').querySelector('.ai-box');
    box.innerHTML = skeleton(2);
    try { const x = await api.post(`/api/insights/${b.dataset.explain}/explain`); box.innerHTML = `<div class="ai-prose" style="font-size:15.5px;margin-top:4px">${esc(x.text)}</div>${aiSource(x.source, x.verification)}`; }
    catch (err) { box.innerHTML = ''; errToast(err); }
    b.disabled = false;
  });
  on(el, 'click', '[data-status]', async (e, b) => { try { await api.put(`/api/insights/${b.dataset.id}`, { status: b.dataset.status }); load(); } catch (err) { errToast(err); } });
  load();
}
