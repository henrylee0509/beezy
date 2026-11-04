import { api } from '../api.js';
import { refreshCounts } from '../app.js';
import { bindApprovals, DEPTS } from '../components.js';
import { aiSource, can, errToast, esc, fmt, icon, md, on, state, statusBadge, toast } from '../ui.js';
import { WavRecorder } from '../voice.js';
import { ordersCard, renderCards } from '../cards.js';
import { bindPicker, pickerHTML } from '../taskpicker.js';

const SUGGESTIONS = [
  'Give me a health check of my business. Check my inventory, upcoming expenses, payroll and cash flow. Tell me what I should prioritise.',
  'Can I afford to restock inventory and pay my employees next month?',
  'Restock all products that have fallen below 20% of their target stock.',
  'Prepare payroll for all active employees.',
  'Which contracts need a decision in the next 30 days, and what are the payment terms with Highland Roasters?',
  'Find the cheapest supplier for our low-stock products, but make sure it arrives before we run out.',
];

const QUICK = [
  ['overview', 'Health check', 'Give me a health check of my business. Check my inventory, upcoming expenses, payroll and cash flow. Tell me what I should prioritise.'],
  ['procurement', 'Restock low items', 'Restock everything below 20% and make sure it arrives before we run out.'],
  ['finance', 'Can I afford it?', 'Can I afford to restock inventory and pay my employees next month?'],
  ['contracts', 'Contracts due', 'Which contracts need a decision in the next 30 days?'],
  ['payroll', 'Prepare payroll', 'Prepare payroll for all active employees.'],
];

let sessionId = null;

export async function render(el, params) {
  let polling = null, running = false, recorder = null, approvalsById = {};
  el.innerHTML = `<div class="page-head"><div><h1>Ask Beezy</h1>
      <div class="lead">Ask in plain language. The Executive Assistant decides which departments to involve, they work through your real records, and anything that spends money waits for your approval.</div></div>
      <div class="actions"><select class="input" id="sessionSel" style="width:auto" aria-label="Conversation"></select><button class="btn" id="newChat">${icon('plus')} New conversation</button></div></div>
    <div class="assistant-layout">
      <section class="panel convo"><div class="messages" id="messages"></div>
        <div class="quick" id="quick">${QUICK.map(([ic, label, q]) => `<button type="button" data-quick="${esc(q)}">${icon(ic)}${esc(label)}</button>`).join('')}</div>
        <form class="composer" id="composer">
          <button type="button" class="icon-btn mic" id="micBtn" aria-label="Hold to speak" title="${state.me.ai.mode === 'real' ? 'Speak (Muse Voice Transcribe)' : 'Voice needs a Meta API key'}">${icon('mic')}</button>
          <textarea id="prompt" rows="1" placeholder="Ask Beezy about stock, cash, payroll, suppliers or contracts…" aria-label="Message"></textarea>
          <button class="btn primary" id="sendBtn" type="submit">${icon('send')} Send</button>
        </form></section>
      <aside class="floor">
        <section class="panel"><div class="panel-head"><h2>Operations floor</h2><span class="sub" id="runMeta">Idle</span></div>
          <div class="panel-body">
            <div class="exec" id="execDesk"><div class="d-icon" style="width:28px;height:28px;border-radius:8px;background:var(--teal);display:grid;place-items:center;color:#fff">${icon('assistant')}</div>
              <div><div class="strong" style="font-weight:650;font-size:13px">Executive Assistant</div><div class="small muted" id="execStatus">Waiting for a request</div></div></div>
            <div class="desks" id="desks">${Object.entries(DEPTS).map(([name, d]) => `<div class="desk idle" data-desk="${esc(name)}" style="--desk:${d.color}">
              <div class="d-name"><span class="d-icon" style="background:${d.color}">${icon(d.icon)}</span>${esc(d.short)}</div><div class="d-status">Idle</div></div>`).join('')}</div>
            <div class="timeline" id="timeline"><p class="muted small" style="margin:0">Every model call and tool call appears here as it happens.</p></div>
          </div></section>
      </aside>
    </div>`;
  const msgs = el.querySelector('#messages');
  const prompt = el.querySelector('#prompt');
  const timeline = el.querySelector('#timeline');
  bindApprovals(msgs, async () => { await loadApprovals(); renderSession(); });

  // Live state the answer cards read, so their buttons always reflect what is pending right now.
  let contractsById = {};
  async function loadApprovals() {
    try { const r = await api.get('/api/approvals?status=ALL'); approvalsById = Object.fromEntries(r.approvals.map(a => [a.id, a])); } catch (e) { approvalsById = {}; }
    if (can('contracts.read')) {
      try { const c = await api.get('/api/contracts'); contractsById = Object.fromEntries(c.contracts.map(x => [x.id, x])); } catch (e) { contractsById = {}; }
    }
  }
  const live = () => ({ approvals: approvalsById, contracts: contractsById, canRestock: can('procurement.write'), canDecideContracts: can('contracts.write') });

  async function loadSessions() {
    const r = await api.get('/api/assistant/sessions');
    const sel = el.querySelector('#sessionSel');
    sel.innerHTML = `<option value="">Past conversations</option>` + r.sessions.map(s => `<option value="${s.id}" ${s.id === sessionId ? 'selected' : ''}>${esc((s.title || 'Conversation').slice(0, 48))}</option>`).join('');
  }

  let session = null;
  async function renderSession() {
    if (!sessionId) {
      session = null;
      msgs.innerHTML = `<div style="margin:auto 0;padding:20px 4px">
        <div class="ai-prose" style="font-size:22px;max-width:30ch;margin-bottom:6px">What should your team look at today?</div>
        <p class="muted" style="margin-bottom:14px">Pick what you want to do from the lists, or type your own question in the box below. You can also hold the microphone and speak.</p>
        ${pickerHTML()}
        <details class="bz-more-ideas"><summary>More example questions</summary>
        <div class="suggestions">${SUGGESTIONS.map(s => `<button class="suggestion" data-suggest="${esc(s)}">${esc(s)}</button>`).join('')}</div></details></div>`;
      bindPicker(msgs, send);
      return;
    }
    session = await api.get(`/api/assistant/sessions/${sessionId}`);
    msgs.innerHTML = session.messages.map(renderMsg).join('') + (running ? `<div class="msg assistant" id="thinking">${progressHTML()}</div>` : '');
    msgs.lastElementChild?.scrollIntoView({ block: 'end', behavior: 'smooth' });
  }

  function renderMsg(m) {
    if (m.role === 'user') return `<div class="msg user">${esc(m.content)}</div>`;
    const meta = m.meta || {};
    const mode = meta.mode === 'real' ? `<span class="badge teal">Muse Spark</span>` : meta.mode === 'fallback' ? `<span class="badge amber">Fallback: Muse Spark unavailable</span>` : meta.mode ? `<span class="badge amber">Mock mode</span>` : '';
    const agents = (meta.agents || []).map(a => `<span class="badge outline" style="border-color:${DEPTS[a]?.color || 'var(--line)'};color:${DEPTS[a]?.color || 'inherit'}">${esc(DEPTS[a]?.short || a)}</span>`).join(' ');
    const cards = renderCards(meta.cards, live());
    const orders = ordersCard(meta.actions || [], approvalsById);
    return `<div class="msg assistant"><div class="who"><b style="color:var(--ink)">Beezy</b> ${mode} ${agents}
        ${meta.duration_ms ? `<span>${(meta.duration_ms / 1000).toFixed(1)}s${meta.model_calls ? ` · ${meta.model_calls} model calls` : ''}</span>` : ''}</div>
      <div class="ai-prose">${md(m.content)}</div>
      ${aiSource(null, meta.verification)}
      ${orders || cards ? `<div class="bz-cards">${orders}${cards}</div>` : ''}</div>`;
  }

  // ---------------- in-chat progress (what the team is doing right now)
  const STAGES = ['Understand', 'Gather data', 'Write answer', 'Check figures'];
  let prog = null, progTimer = null;
  function progReset() {
    prog = { stage: 0, start: Date.now(), mode: state.me.ai.mode, head: 'Reading your request', agents: [], note: '' };
    clearInterval(progTimer);
    progTimer = setInterval(() => { const t = msgs.querySelector('#progTime'); if (t && prog) t.textContent = fmtSecs(Date.now() - prog.start); hint(); }, 1000);
  }
  const fmtSecs = (ms) => { const s = Math.floor(ms / 1000); return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s`; };
  const agentRow = (name) => {
    let a = prog.agents.find(x => x.name === name);
    if (!a) { a = { name, status: 'queued', text: 'Waiting to start', tools: 0 }; prog.agents.push(a); }
    return a;
  };
  function hint() {
    const h = msgs.querySelector('#progHint');
    if (!h || !prog) return;
    const slow = Date.now() - prog.start > 7000;
    h.hidden = !slow;
    if (slow) h.textContent = prog.mode === 'real'
      ? 'Live answers take a little longer: each department reasons with Muse Spark and calls its own tools. The answer appears here when it is ready.'
      : 'Still working. The answer appears here when it is ready.';
  }
  function progressHTML() {
    if (!prog) return '';
    const done = prog.agents.filter(a => a.status === 'done').length;
    const steps = STAGES.map((label, i) => `<li class="${i < prog.stage ? 'done' : i === prog.stage ? 'now' : ''}">
        <span class="dot">${i < prog.stage ? icon('check') : ''}</span>${label}</li>`).join('');
    const rows = prog.agents.map(a => {
      const d = DEPTS[a.name] || { color: 'var(--ink-2)', icon: 'assistant', short: a.name };
      return `<div class="bz-prow ${a.status}" style="--c:${d.color}">
        <span class="d-icon" style="background:${d.color}">${icon(d.icon)}</span>
        <div class="who2"><b>${esc(d.short)}</b><span>${esc(a.text)}</span></div>
        <span class="st">${a.status === 'done' ? `${icon('check')} ${a.tools ? `${a.tools} tool${a.tools > 1 ? 's' : ''}` : 'Done'}` : a.status === 'working' ? '<i class="bar"></i>' : 'Queued'}</span></div>`;
    }).join('');
    return `<div class="bz-prog" role="status" aria-live="polite">
      <div class="bz-phead"><span class="bz-hexspin" aria-hidden="true"></span>
        <div><b>${esc(prog.head)}</b>${prog.agents.length && prog.stage === 1 ? `<span class="sub">${done} of ${prog.agents.length} departments done</span>` : ''}</div>
        <span class="time" id="progTime">${fmtSecs(Date.now() - prog.start)}</span></div>
      <ol class="bz-steps">${steps}</ol>
      ${rows ? `<div class="bz-prows">${rows}</div>` : ''}
      ${prog.note ? `<div class="bz-pnote">${esc(prog.note)}</div>` : ''}
      <p class="bz-phint" id="progHint" hidden></p>
      ${prog.stage >= 1 ? '<div class="bz-skel" aria-hidden="true"><i></i><i></i></div>' : ''}</div>`;
  }
  function progEvent(ev) {
    if (!prog) return;
    const d = ev.data || {};
    const ea = ev.agent === 'Executive Assistant';
    if (ev.kind === 'run_started') { prog.mode = d.mode || prog.mode; prog.head = 'Understanding your request'; }
    if (ev.kind === 'thinking' && ea) {
      if ((d.round || 1) === 1) { prog.stage = 0; prog.head = 'Muse Spark is planning which departments to involve'; }
      else { prog.stage = 2; prog.head = 'Writing your answer from the findings'; }
    }
    if (ev.kind === 'plan' && d.plan) {
      prog.stage = 1;
      d.plan.forEach(p => { const a = agentRow(p.agent); if (a.status === 'done') a.status = 'queued'; });
      const n = prog.agents.length;
      prog.head = `Gathering data from ${n} department${n > 1 ? 's' : ''}${n > 1 ? ' at once' : ''}`;
    }
    if (DEPTS[ev.agent]) {
      const a = agentRow(ev.agent);
      if (ev.kind === 'agent_started') { a.status = 'working'; a.text = 'Starting'; }
      if (ev.kind === 'thinking') { a.status = 'working'; a.text = ev.title; }
      if (ev.kind === 'tool_call') { a.status = 'working'; a.text = ev.title; a.tools++; }
      if (ev.kind === 'agent_finished') { a.status = 'done'; a.text = 'Finished'; }
      if (ev.kind === 'tool_error' || ev.kind === 'agent_error') a.text = ev.title;
      if (prog.stage === 1 && prog.agents.every(x => x.status === 'done')) { prog.stage = 2; prog.head = 'Writing your answer from the findings'; }
    }
    if (ev.kind === 'error' && ea) prog.note = ev.title;
    if (ev.kind === 'verification') { prog.stage = 3; prog.head = 'Checking every figure against your records'; }
    const box = msgs.querySelector('#thinking');
    if (box) { box.innerHTML = progressHTML(); hint(); }
  }

  // ---------------- operations floor
  function resetFloor() {
    el.querySelectorAll('.desk').forEach(d => { d.className = 'desk idle'; d.querySelector('.d-status').textContent = 'Idle'; });
    el.querySelector('#execDesk').classList.add('working');
    el.querySelector('#execStatus').textContent = 'Reading your request';
    timeline.innerHTML = '';
  }
  const deskFor = (agent) => el.querySelector(`.desk[data-desk="${CSS.escape(agent)}"]`);
  function applyEvent(ev) {
    const d = ev.data || {};
    const desk = deskFor(ev.agent);
    if (ev.kind === 'plan') el.querySelector('#execStatus').textContent = ev.title;
    if (ev.kind === 'model_call' && ev.agent === 'Executive Assistant') el.querySelector('#execStatus').textContent = ev.title;
    if (ev.kind === 'thinking' && ev.agent === 'Executive Assistant') el.querySelector('#execStatus').textContent = ev.title + '…';
    if (desk) {
      const st = desk.querySelector('.d-status');
      if (ev.kind === 'agent_started') { desk.className = 'desk working'; st.textContent = d.task ? `Task: ${d.task}`.slice(0, 120) : 'Working'; }
      if (ev.kind === 'tool_call') { desk.className = 'desk working'; st.textContent = ev.title + '…'; }
      if (ev.kind === 'model_call') st.textContent = 'Reasoning with Muse Spark…';
      if (ev.kind === 'thinking') { desk.className = 'desk working'; st.textContent = ev.title + '…'; }
      if (ev.kind === 'agent_finished') { desk.className = 'desk done'; st.textContent = `Done${(d.tools_used || []).length ? ` · ${d.tools_used.length} tool call${d.tools_used.length > 1 ? 's' : ''}` : ''}`; }
      if (ev.kind === 'tool_error' || ev.kind === 'agent_error') st.textContent = ev.title;
    }
    if (ev.kind === 'final') { el.querySelector('#execDesk').classList.remove('working'); el.querySelector('#execStatus').textContent = d.error ? 'Request failed' : 'Answer delivered'; }
    if (ev.kind === 'run_started') el.querySelector('#runMeta').textContent = d.mode === 'real' ? `Live · ${d.model}` : 'Mock mode';
    const isAction = ev.kind === 'tool_call' && d.action;
    let detail = '';
    if (ev.kind === 'plan' && d.plan) detail = d.plan.map(p => `${p.agent}: ${p.task}`).join(' | ');
    if (ev.kind === 'model_call' && d.latency_ms) detail = `${d.latency_ms} ms${d.tool_calls && d.tool_calls.length ? ' → ' + d.tool_calls.join(', ') : ''}`;
    if (ev.kind === 'tool_result' && d.ms !== undefined) detail = `${d.ms} ms`;
    if (ev.kind === 'verification') detail = d.unverified && d.unverified.length ? `Unverified: ${d.unverified.join(', ')}` : 'All figures traced to data';
    if (ev.kind === 'error') detail = d.error || '';
    const row = document.createElement('div');
    row.className = `ev ${ev.kind}${isAction ? ' action' : ''}`;
    row.innerHTML = `<span class="e-dot"></span><div><span class="e-agent">${esc(DEPTS[ev.agent]?.short || ev.agent)}</span> <span class="e-title">${esc(isAction ? 'Action: ' + ev.title : ev.title)}</span>
      ${detail ? `<div class="muted" style="font-size:11px;word-break:break-word">${esc(detail.slice(0, 260))}</div>` : ''}</div>`;
    timeline.appendChild(row);
    timeline.scrollTop = timeline.scrollHeight;
  }

  async function send(text) {
    text = (text || '').trim();
    if (!text || running) return;
    running = true;
    el.querySelector('#sendBtn').disabled = true;
    prompt.value = '';
    try {
      const r = await api.post('/api/assistant/chat', { message: text, session_id: sessionId });
      sessionId = r.session_id;
      resetFloor();
      progReset();
      await renderSession();
      let after = r.events_after;
      const startedAt = Date.now();
      const tick = async () => {
        try {
          const e = await api.get(`/api/assistant/sessions/${sessionId}/events?after=${after}&run_id=${r.run_id}`);
          e.events.forEach(ev => { applyEvent(ev); progEvent(ev); after = ev.id; });
          const finished = e.events.some(ev => ev.kind === 'final') || (e.run && e.run.status !== 'running') || (!e.run && !e.events.length && Date.now() - startedAt > 15000);
          if (finished) {
            stop();
            clearInterval(progTimer); prog = null;
            running = false;
            el.querySelector('#sendBtn').disabled = false;
            await loadApprovals();
            await renderSession();
            loadSessions();
            refreshCounts();
            return;
          }
        } catch (err) { /* transient */ }
        polling = setTimeout(tick, 350);
      };
      tick();
    } catch (e) {
      running = false;
      el.querySelector('#sendBtn').disabled = false;
      errToast(e);
    }
  }
  const stop = () => { if (polling) clearTimeout(polling); polling = null; };

  el.querySelector('#composer').onsubmit = (e) => { e.preventDefault(); send(prompt.value); };
  prompt.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(prompt.value); } });
  prompt.addEventListener('input', () => { prompt.style.height = 'auto'; prompt.style.height = Math.min(160, prompt.scrollHeight) + 'px'; });
  on(msgs, 'click', '[data-suggest]', (e, b) => send(b.dataset.suggest));
  on(el, 'click', '[data-quick]', (e, b) => send(b.dataset.quick));
  on(msgs, 'click', '[data-restock]', async (e, b) => {
    b.disabled = true;
    try {
      const r = await api.post('/api/procurement/draft-restock', { product_ids: b.dataset.restock.split(',').map(Number) });
      const n = (r.created || []).length;
      toast(n ? `${n} order${n > 1 ? 's' : ''} drafted and added to To approve` : 'Nothing needed restocking', 'ok');
      refreshCounts();
      await loadApprovals();
      renderSession();
    } catch (err) { errToast(err); b.disabled = false; }
  });
  on(msgs, 'click', '[data-contract-action]', async (e, b) => {
    const [id, action, months] = b.dataset.contractAction.split('|');
    b.disabled = true;
    try {
      await api.post(`/api/contracts/${id}/actions`, { action, ...(months ? { months: Number(months) } : {}) });
      toast('Added to To approve. Approve it here or on the To approve page.', 'ok');
      refreshCounts();
      await loadApprovals();
      renderSession();
    } catch (err) { errToast(err); b.disabled = false; }
  });
  on(msgs, 'click', '[data-approve-all]', async (e, b) => {
    b.disabled = true;
    const ids = b.dataset.approveAll.split(',');
    let ok = 0;
    for (const id of ids) {
      try { await api.post(`/api/approvals/${id}/approve`); ok++; } catch (err) { errToast(err); }
    }
    toast(`${ok} of ${ids.length} approved. Orders submitted to suppliers (simulated).`, 'ok');
    refreshCounts();
    await loadApprovals();
    renderSession();
  });
  el.querySelector('#newChat').onclick = () => { sessionId = null; stop(); running = false; renderSession(); loadSessions(); };
  el.querySelector('#sessionSel').onchange = (e) => { sessionId = e.target.value ? Number(e.target.value) : null; renderSession(); };

  // push-to-talk
  const mic = el.querySelector('#micBtn');
  const startRec = async () => {
    if (state.me.ai.mode !== 'real') { toast('Voice input uses Muse Voice Transcribe. Add MODEL_API_KEY to enable it.', 'error'); return; }
    if (recorder) return;
    try { recorder = new WavRecorder(); await recorder.start(); mic.classList.add('rec'); } catch (e) { recorder = null; errToast(new Error('Microphone unavailable: ' + e.message)); }
  };
  const stopRec = async () => {
    if (!recorder) return;
    const r = recorder; recorder = null; mic.classList.remove('rec');
    const wav = await r.stop();
    if (wav.size < 8000) return toast('Hold the button while you speak');
    const fd = new FormData();
    fd.append('audio', wav, 'speech.wav');
    try {
      prompt.placeholder = 'Transcribing with Muse Voice Transcribe…';
      const t = await api.upload('/api/assistant/voice', fd);
      prompt.placeholder = 'Ask Beezy…';
      if (t.text) { prompt.value = t.text; send(t.text); } else toast('No speech detected');
    } catch (e) { prompt.placeholder = 'Ask Beezy…'; errToast(e); }
  };
  mic.addEventListener('pointerdown', startRec);
  mic.addEventListener('pointerup', stopRec);
  mic.addEventListener('pointerleave', stopRec);
  mic.addEventListener('keydown', (e) => { if ((e.key === ' ' || e.key === 'Enter') && !e.repeat) { e.preventDefault(); startRec(); } });
  mic.addEventListener('keyup', (e) => { if (e.key === ' ' || e.key === 'Enter') stopRec(); });

  await loadApprovals();
  await loadSessions().catch(() => {});
  await renderSession();
  const q = params.get('q');
  if (q) { history.replaceState(null, '', '#/assistant'); sessionId = null; send(q); }
  return () => { stop(); };
}
