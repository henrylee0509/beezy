import { api } from '../api.js';
import { errToast, esc, fmt, on, skeleton } from '../ui.js';

export async function render(el) {
  let actor = '', offset = 0, tab = 'audit';
  el.innerHTML = `<div class="page-head"><div><h1>Activity log</h1><div class="lead">Every action by people, AI agents and automations, plus every call made to the Meta Model API.</div></div></div>
    <div class="tabs"><button data-tab="audit" class="on">Audit trail</button><button data-tab="ai">AI requests</button></div>
    <div id="filters" class="row" style="margin-bottom:12px">${[['', 'Everyone'], ['USER', 'People'], ['AGENT', 'AI agents'], ['AUTOMATION', 'Automations'], ['SYSTEM', 'System']].map(([k, l]) => `<button class="btn sm ${k === actor ? 'primary' : ''}" data-a="${k}">${l}</button>`).join('')}</div>
    <section class="panel"><div class="panel-body table-wrap" id="tbl">${skeleton(8)}</div></section>`;
  const tbl = el.querySelector('#tbl');
  async function audit() {
    const r = await api.get(`/api/audit?limit=50&offset=${offset}${actor ? '&actor_type=' + actor : ''}`);
    const tag = { USER: '', AGENT: 'teal', AUTOMATION: 'blue', SYSTEM: '' };
    tbl.innerHTML = `<table class="t"><thead><tr><th>When</th><th>Actor</th><th>Action</th><th>Record</th><th>Details</th></tr></thead><tbody>${r.items.map(x => `<tr>
      <td class="small num">${fmt.datetime(x.created_at)}</td><td><span class="badge ${tag[x.actor_type]}">${esc(x.actor_type.toLowerCase())}</span> <span class="small">${esc(x.actor)}</span></td>
      <td>${esc(x.action)}</td><td class="small muted">${esc(x.entity || '')}${x.entity_id ? ' #' + x.entity_id : ''}</td>
      <td class="small muted" style="max-width:420px;word-break:break-word">${esc(JSON.stringify(x.details)).slice(0, 220)}</td></tr>`).join('')}</tbody></table>
      <div class="row" style="justify-content:space-between;margin-top:10px"><span class="small muted">${r.total} entries</span><span class="row"><button class="btn sm" id="prev" ${offset ? '' : 'disabled'}>Newer</button><button class="btn sm" id="next" ${offset + 50 < r.total ? '' : 'disabled'}>Older</button></span></div>`;
    tbl.querySelector('#prev').onclick = () => { offset = Math.max(0, offset - 50); audit(); };
    tbl.querySelector('#next').onclick = () => { offset += 50; audit(); };
  }
  async function ai() {
    const r = await api.get('/api/ai/requests?limit=100');
    tbl.innerHTML = r.requests.length ? `<table class="t"><thead><tr><th>When</th><th>Agent</th><th>Model</th><th>Status</th><th class="right">Latency</th><th class="right">Tokens</th><th>Error</th></tr></thead><tbody>${r.requests.map(x => `<tr>
      <td class="small num">${fmt.datetime(x.created_at)}</td><td>${esc(x.agent || '')}</td><td class="small">${esc(x.model || '')}</td><td><span class="badge ${x.status === 'ok' ? 'green' : 'red'}">${esc(x.status)}</span></td>
      <td class="right num">${x.latency_ms ?? '—'} ms</td><td class="right num">${(x.prompt_tokens || 0) + (x.completion_tokens || 0) || '—'}</td><td class="small muted">${esc(x.error || '')}</td></tr>`).join('')}</tbody></table>`
      : '<p class="muted">No Meta Model API calls yet. In mock mode no requests are sent.</p>';
  }
  const show = () => (tab === 'audit' ? audit() : ai()).catch(errToast);
  on(el, 'click', '[data-a]', (e, b) => { actor = b.dataset.a; offset = 0; el.querySelectorAll('[data-a]').forEach(x => x.classList.toggle('primary', x === b)); show(); });
  on(el, 'click', '[data-tab]', (e, b) => { tab = b.dataset.tab; el.querySelectorAll('[data-tab]').forEach(x => x.classList.toggle('on', x === b)); el.querySelector('#filters').classList.toggle('hidden', tab !== 'audit'); show(); });
  show();
}
