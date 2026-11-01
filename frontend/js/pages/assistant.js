import { api } from '../api.js';
import { refreshCounts } from '../app.js';
import { bindApprovals, DEPTS } from '../components.js';
import { aiSource, errToast, esc, fmt, icon, md, on, state, statusBadge, toast } from '../ui.js';
import { WavRecorder } from '../voice.js';

const SUGGESTIONS = [
  'Give me a health check of my business. Check my inventory, upcoming expenses, payroll and cash flow. Tell me what I should prioritise.',
  'Can I afford to restock inventory and pay my employees next month?',
  'Restock all products that have fallen below 20% of their target stock.',
  'Prepare payroll for all active employees.',
  'Which contracts need a decision in the next 30 days, and what are the payment terms with Highland Roasters?',
  'Find the cheapest supplier for our low-stock products, but make sure it arrives before we run out.',
];

let sessionId = null;

export async function render(el, params) {
  let polling = null, running = false, recorder = null, approvalsById = {};
  el.innerHTML = `<div class="page-head"><div><h1>AI Business Assistant</h1>
      <div class="lead">Ask in plain language. The Executive Assistant decides which departments to involve, they work through your real records, and anything that spends money waits for your approval.</div></div>
      <div class="actions"><select class="input" id="sessionSel" style="width:auto" aria-label="Conversation"></select><button class="btn" id="newChat">${icon('plus')} New conversation</button></div></div>
    <div class="assistant-layout">
      <section class="panel convo"><div class="messages" id="messages"></div>
        <form class="composer" id="composer">
          <button type="button" class="icon-btn mic" id="micBtn" aria-label="Hold to speak" title="${state.me.ai.mode === 'real' ? 'Speak (Muse Voice Transcribe)' : 'Voice needs a Meta API key'}">${icon('mic')}</button>
          <textarea id="prompt" rows="1" placeholder="Ask BusinessPilot about stock, cash, payroll, suppliers or contracts…" aria-label="Message"></textarea>
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

  async function loadApprovals() {
    try { const r = await api.get('/api/approvals?status=ALL'); approvalsById = Object.fromEntries(r.approvals.map(a => [a.id, a])); } catch (e) { approvalsById = {}; }
  }

  async function loadSessions() {
    const r = await api.get('/api/assistant/sessions');
    const sel = el.querySelector('#sessionSel');
    sel.innerHTML = `<option value="">New conversation</option>` + r.sessions.map(s => `<option value="${s.id}" ${s.id === sessionId ? 'selected' : ''}>${esc((s.title || 'Conversation').slice(0, 48))}</option>`).join('');
  }

  let session = null;
  async function renderSession() {
    if (!sessionId) {
      session = null;
      msgs.innerHTML = `<div style="margin:auto 0;padding:20px 4px">
        <div class="ai-prose" style="font-size:22px;max-width:30ch;margin-bottom:6px">What should your team look at today?</div>
        <p class="muted" style="margin-bottom:14px">Try one of these, or ask your own question. You can also hold the microphone and speak.</p>
        <div class="suggestions">${SUGGESTIONS.map(s => `<button class="suggestion" data-suggest="${esc(s)}">${esc(s)}</button>`).join('')}</div></div>`;
      return;
    }
    session = await api.get(`/api/assistant/sessions/${sessionId}`);
    msgs.innerHTML = session.messages.map(renderMsg).join('') + (running ? `<div class="msg assistant" id="thinking"><div class="who">BusinessPilot</div><div class="muted">Working on it — follow the operations floor.</div></div>` : '');
    msgs.lastElementChild?.scrollIntoView({ block: 'end', behavior: 'smooth' });
  }

  function renderMsg(m) {
    if (m.role === 'user') return `<div class="msg user">${esc(m.content)}</div>`;
    const meta = m.meta || {};
    const mode = meta.mode === 'real' ? `<span class="badge teal">Muse Spark</span>` : meta.mode === 'fallback' ? `<span class="badge amber">Fallback: Muse Spark unavailable</span>` : meta.mode ? `<span class="badge amber">Mock mode</span>` : '';
    const agents = (meta.agents || []).map(a => `<span class="badge outline" style="border-color:${DEPTS[a]?.color || 'var(--line)'};color:${DEPTS[a]?.color || 'inherit'}">${esc(DEPTS[a]?.short || a)}</span>`).join(' ');
    const actions = (meta.actions || []).map(a => {
      const ap = a.approval_id ? approvalsById[a.approval_id] : null;
      const status = ap ? ap.status : a.status;
      const canDecide = ap && ap.status === 'PENDING' && ap.can_decide !== false;
      return `<div class="action-card"><div class="ac-main"><div class="ac-title">${esc(a.title)}</div><div class="small muted">${esc(a.detail || '')}</div></div>
        ${a.amount ? `<span class="num" style="font-weight:650">${fmt.money(a.amount)}</span>` : ''}
        ${canDecide ? `<div class="row"><button class="btn sm" data-reject="${a.approval_id}">Reject</button><button class="btn sm primary" data-approve="${a.approval_id}">Approve</button></div>`
          : (ap && ap.status === 'PENDING' ? `<span class="badge amber">Needs ${esc(ap.required_role.toLowerCase())}</span>` : statusBadge(status === 'APPROVED' && a.type === 'PURCHASE_ORDER' ? 'SUBMITTED' : status))}
        <a class="small" href="${a.link}">Open</a></div>`;
    }).join('');
    return `<div class="msg assistant"><div class="who"><b style="color:var(--ink)">BusinessPilot</b> ${mode} ${agents}
        ${meta.duration_ms ? `<span>${(meta.duration_ms / 1000).toFixed(1)}s${meta.model_calls ? ` · ${meta.model_calls} model calls` : ''}</span>` : ''}</div>
      <div class="ai-prose">${md(m.content)}</div>
      ${actions ? `<div style="margin-top:6px"><div class="small muted" style="font-weight:600">Prepared for your approval — nothing has been submitted or paid yet</div>${actions}</div>` : ''}
      ${aiSource(null, meta.verification)}</div>`;
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
    if (desk) {
      const st = desk.querySelector('.d-status');
      if (ev.kind === 'agent_started') { desk.className = 'desk working'; st.textContent = d.task ? `Task: ${d.task}`.slice(0, 120) : 'Working'; }
      if (ev.kind === 'tool_call') { desk.className = 'desk working'; st.textContent = ev.title + '…'; }
      if (ev.kind === 'model_call') st.textContent = 'Reasoning with Muse Spark…';
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
      await renderSession();
      let after = r.events_after;
      const startedAt = Date.now();
      const tick = async () => {
        try {
          const e = await api.get(`/api/assistant/sessions/${sessionId}/events?after=${after}&run_id=${r.run_id}`);
          e.events.forEach(ev => { applyEvent(ev); after = ev.id; });
          const finished = e.events.some(ev => ev.kind === 'final') || (e.run && e.run.status !== 'running') || (!e.run && !e.events.length && Date.now() - startedAt > 15000);
          if (finished) {
            stop();
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
      prompt.placeholder = 'Ask BusinessPilot…';
      if (t.text) { prompt.value = t.text; send(t.text); } else toast('No speech detected');
    } catch (e) { prompt.placeholder = 'Ask BusinessPilot…'; errToast(e); }
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
