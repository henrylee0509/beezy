import { api } from '../api.js';
import { contractTimeline } from '../cards.js';
import { can, errToast, esc, field, fmt, icon, modal, on, skeleton, toast } from '../ui.js';

export async function render(el) {
  el.innerHTML = `<div class="page-head"><div><h1>Contracts</h1><div class="lead">Upload agreements and the Contract Agent extracts dates, payment terms and renewal clauses, then reminds you before deadlines.</div></div>
    <div class="actions"><a class="btn" href="#/assistant?q=${encodeURIComponent('Which contracts expire or need a renewal decision in the next 60 days? Cite the clauses.')}">${icon('sparkle')} Ask about contracts</a>
    ${can('contracts.write') ? `<label class="btn primary" style="cursor:pointer">${icon('upload')} Upload contract<input type="file" id="up" accept=".pdf,.docx,.txt" hidden></label>` : ''}</div></div>
    ${can('contracts.write') ? `<label class="dropzone" id="drop" for="up"><span class="bz-hex">${icon('upload')}</span>
      <div><b>Drop a contract here, or click to upload</b><br><span>PDF, Word or text. The Contract agent extracts the term, payment terms, renewal and notice clauses with page references, then schedules reminders.</span></div></label>` : ''}
    <section class="panel" style="margin-bottom:16px"><div class="panel-head"><span class="bz-hex sm">${icon('contracts')}</span><h2>Deadlines, next 90 days</h2><span class="sub">Red: last day to give notice · Yellow: auto-renews · Black: expires</span></div>
      <div class="panel-body" id="timeline">${skeleton(2)}</div></section>
    <div class="row" style="margin-bottom:12px"><input class="input" id="q" placeholder="Search clauses, parties or terms across all contracts" style="max-width:420px"><span class="small muted" id="disclaimer"></span></div>
    <div id="search"></div>
    <div class="stack"><section class="panel"><div class="panel-body table-wrap" id="tbl">${skeleton(5)}</div></section>
    <section class="panel"><div class="panel-head"><h2>Upcoming reminders</h2><span class="sub">Next 30 days</span></div><div class="panel-body" id="rem"></div></section></div>`;
  let data;

  async function load() {
    try { data = await api.get('/api/contracts'); } catch (e) { return errToast(e); }
    el.querySelector('#disclaimer').textContent = data.disclaimer;
    el.querySelector('#timeline').innerHTML = contractTimeline(data.contracts);
    el.querySelector('#tbl').innerHTML = `<table class="t"><thead><tr><th>Contract</th><th>Counterparty</th><th>Start</th><th>Expires</th><th>Renewal</th><th class="right">Value</th><th>Source</th></tr></thead><tbody>
      ${data.contracts.map(c => `<tr><td class="strong"><a href="#" data-open="${c.id}">${esc(c.title)}</a><div class="small muted">${esc(c.category)}</div></td><td>${esc(c.counterparty || '—')}</td>
        <td>${fmt.date(c.start_date)}</td><td>${fmt.date(c.end_date)}<div class="small muted">${c.days_to_expiry !== null ? fmt.rel(c.days_to_expiry) : ''}</div></td>
        <td>${renewal(c)}</td><td class="right num">${c.value ? fmt.money(c.value) + (c.value_period ? `<div class="small muted">per ${esc(c.value_period)}</div>` : '') : '—'}</td>
        <td class="small muted">${esc(c.extraction_source || '')}</td></tr>`).join('') || '<tr><td colspan="7" class="muted">No contracts yet. Upload a PDF or Word file to get started.</td></tr>'}</tbody></table>`;
    el.querySelector('#rem').innerHTML = data.reminders.length ? data.reminders.map(r => `<div class="alert-row"><span class="sev warning"></span><div><div class="a-title">${esc(r.title)}</div><div class="a-detail">${esc(r.label)}, ${fmt.date(r.remind_on)} (${fmt.rel(Math.round((new Date(r.remind_on + 'T00:00:00') - new Date(new Date().toDateString())) / 864e5))})</div></div></div>`).join('')
      : `<p class="muted">No reminders in the next 30 days. Reminders fire 60, 30 and 7 days before expiry and before notice deadlines.</p>`;
  }
  const renewal = (c) => {
    if (c.notice_deadline && c.days_to_notice_deadline !== null && c.days_to_notice_deadline >= 0 && c.days_to_notice_deadline <= 30)
      return `<span class="badge red">Notice by ${fmt.date(c.notice_deadline)}</span><div class="small muted">auto-renews</div>`;
    const map = { 'Active': 'green', 'Expiring soon': 'amber', 'Auto-renews soon': 'amber', 'Expired': '', 'Auto-renewed / check': 'amber' };
    return `<span class="badge ${map[c.renewal_status] || ''}">${esc(c.renewal_status)}</span>${c.auto_renew ? '<div class="small muted">auto-renews</div>' : ''}`;
  };

  on(el, 'click', '[data-open]', async (e, a) => { e.preventDefault(); openContract(Number(a.dataset.open)); });
  async function openContract(id, highlight = '') {
    const pr = modal({ title: 'Contract', wide: true, noFooter: true, body: `<div id="cd">${skeleton(6)}</div>` });
    try {
      const c = await api.get(`/api/contracts/${id}`);
      const doc = c.document_id ? await api.get(`/api/documents/${c.document_id}`) : null;
      const hl = (text) => {
        let t = esc(text);
        const terms = [highlight, ...(c.key_clauses || []).map(k => k.text)].filter(x => x && x.length > 3);
        for (const term of terms) {
          const words = esc(term).split(/\s+/).slice(0, 6).join(' ');
          if (words.length > 5) t = t.split(words).join(`<mark>${words}</mark>`);
        }
        return t;
      };
      document.getElementById('cd').innerHTML = `<h2 style="margin-bottom:4px">${esc(c.title)}</h2><p class="muted">${esc(c.category)} with ${esc(c.counterparty || '—')} · extracted by ${esc(c.extraction_source || 'manual entry')}</p>
        <div class="grid g2" style="margin-top:12px"><div>
          <dl class="kv"><dt>Term</dt><dd>${fmt.date(c.start_date)} to ${fmt.date(c.end_date)} (${fmt.rel(c.days_to_expiry)})</dd>
          <dt>Value</dt><dd>${c.value ? fmt.money(c.value) + (c.value_period ? ' per ' + esc(c.value_period) : '') : '—'}</dd><dt>Payment terms</dt><dd>${esc(c.payment_terms || '—')}</dd>
          <dt>Renewal</dt><dd>${c.auto_renew ? `Automatic${c.notice_days ? `, unless ${c.notice_days} days' notice` : ''}` : 'Does not auto-renew'}</dd>
          ${c.notice_deadline ? `<dt>Notice deadline</dt><dd><b>${fmt.date(c.notice_deadline)}</b> (${fmt.rel(c.days_to_notice_deadline)})</dd>` : ''}
          <dt>Termination</dt><dd>${esc(c.termination_clause || '—')}</dd></dl>
          ${c.summary ? `<h3 style="margin:14px 0 6px">Summary</h3><div class="ai-prose" style="font-size:16px">${esc(c.summary)}</div>` : ''}
          <h3 style="margin:14px 0 6px">Key clauses</h3>${(c.key_clauses || []).map(k => `<div class="action-card" style="margin-top:6px"><div class="ac-main"><div class="ac-title">${esc(k.type || 'Clause')}</div><div class="small">${esc(k.text)}</div></div>${k.page ? `<span class="badge outline">page ${k.page}</span>` : ''}</div>`).join('') || '<p class="muted">None extracted.</p>'}
          <h3 style="margin:14px 0 6px">Reminders</h3><table class="t"><tbody>${c.reminders.map(r => `<tr><td>${fmt.date(r.remind_on)}</td><td>${esc(r.label)}</td><td>${esc(r.status.toLowerCase())}</td></tr>`).join('') || '<tr><td class="muted">No end date, so no reminders.</td></tr>'}</tbody></table>
          <p class="small muted" style="margin-top:12px">${esc(c.disclaimer)}</p>
          <div class="row" style="margin-top:10px"><a class="btn sm" href="#/assistant?q=${encodeURIComponent(`Summarise the obligations and renewal terms in "${c.title}" and tell me what I should decide, citing pages.`)}">${icon('sparkle')} Ask the Contract Agent</a>
          ${can('contracts.write') ? `<button class="btn sm ghost" id="editC">Correct details</button>` : ''}
          ${c.document_id && c.filename && !/Demo\)\.txt$/.test(c.filename) ? `<a class="btn sm ghost" href="/api/documents/${c.document_id}/file" id="dl">${icon('download')} Original file</a>` : ''}</div>
        </div>
        <div><h3 style="margin-bottom:8px">Document ${doc ? `<span class="muted small">${esc(doc.filename)}</span>` : ''}</h3>
          <div style="max-height:62vh;overflow:auto">${doc ? doc.pages.map((p, i) => `<div class="doc-page"><div class="pg">Page ${i + 1}</div>${hl(p)}</div>`).join('') : '<p class="muted">No source document.</p>'}</div></div></div>`;
      document.getElementById('dl')?.addEventListener('click', async (ev) => {
        ev.preventDefault();
        const { download } = await import('../api.js');
        download(`/api/documents/${c.document_id}/file`, c.filename).catch(errToast);
      });
      document.getElementById('editC')?.addEventListener('click', async () => {
        document.querySelector('.modal-back')?.remove();
        const f = await modal({ title: 'Correct contract details', body: `<div class="form-grid">${field('title', 'Title', { value: c.title, full: true })}${field('counterparty', 'Counterparty', { value: c.counterparty || '' })}
          ${field('category', 'Category', { options: data.categories, value: c.category })}${field('start_date', 'Start', { type: 'date', value: c.start_date || '' })}${field('end_date', 'End', { type: 'date', value: c.end_date || '' })}
          ${field('value', 'Value', { type: 'number', step: '0.01', value: c.value ?? '' })}${field('notice_days', 'Notice days', { type: 'number', value: c.notice_days ?? '' })}
          ${field('payment_terms', 'Payment terms', { value: c.payment_terms || '', full: true })}${field('auto_renew', 'Renews automatically', { type: 'checkbox', value: !!c.auto_renew, full: true })}</div>` });
        if (!f) return;
        const body = { ...f, value: f.value === '' ? null : Number(f.value), notice_days: f.notice_days === '' ? null : Number(f.notice_days) };
        for (const k of ['start_date', 'end_date']) if (!body[k]) delete body[k];
        try { await api.put(`/api/contracts/${c.id}`, body); toast('Saved; reminders updated', 'ok'); load(); } catch (err) { errToast(err); }
      });
    } catch (err) { document.getElementById('cd').innerHTML = `<p class="muted">${esc(err.message)}</p>`; }
    await pr;
  }

  const drop = el.querySelector('#drop');
  if (drop) {
    ['dragenter', 'dragover'].forEach(ev => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add('over'); }));
    ['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove('over'); }));
    drop.addEventListener('drop', (e) => { const f = e.dataTransfer.files[0]; if (f) uploadFile(f); });
  }
  el.querySelector('#up')?.addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    await uploadFile(file);
    e.target.value = '';
  });
  async function uploadFile(file) {
    const fd = new FormData();
    fd.append('file', file);
    toast(`Reading ${file.name} with the Contract Agent…`);
    try {
      const r = await api.upload('/api/contracts/upload', fd);
      toast(`Extracted "${r.contract.title}" from ${r.pages} page(s) (${r.extraction_source})`, 'ok');
      await load();
      openContract(r.contract.id);
    } catch (err) { errToast(err); }
  }

  let t;
  el.querySelector('#q').addEventListener('input', (e) => {
    clearTimeout(t);
    const q = e.target.value.trim();
    t = setTimeout(async () => {
      const box = el.querySelector('#search');
      if (q.length < 2) { box.innerHTML = ''; return; }
      try {
        const r = await api.get('/api/contracts/search?q=' + encodeURIComponent(q));
        box.innerHTML = `<section class="panel" style="margin-bottom:16px"><div class="panel-head"><h2>Results for “${esc(q)}”</h2><span class="sub">${r.results.length} contract(s)</span></div><div class="panel-body">
          ${r.results.map(x => `<div style="margin-bottom:10px"><a href="#" data-open-hl="${x.id}" data-term="${esc(q)}" class="strong">${esc(x.title)}</a> <span class="muted small">${esc(x.counterparty || '')}</span>
            ${x.matches.map(m => `<div class="small" style="margin-top:3px"><span class="badge outline">page ${m.page}</span> …${esc(m.excerpt)}…</div>`).join('')}</div>`).join('') || '<p class="muted">No matches.</p>'}</div></section>`;
      } catch (err) { errToast(err); }
    }, 300);
  });
  on(el, 'click', '[data-open-hl]', (e, a) => { e.preventDefault(); openContract(Number(a.dataset.openHl), a.dataset.term); });

  load();
}
