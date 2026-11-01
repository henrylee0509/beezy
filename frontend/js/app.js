// App shell: auth, router, navigation, global behaviours.
import { api, getToken, setToken, setUnauthorizedHandler } from './api.js';
import { can, errToast, esc, icon, on, state, toast } from './ui.js';

const PAGES = [
  { path: 'overview', label: 'Overview', icon: 'overview', perm: 'dashboard.read' },
  { path: 'assistant', label: 'AI Business Assistant', icon: 'assistant', perm: 'assistant.use' },
  { path: 'inventory', label: 'Inventory', icon: 'inventory', perm: 'inventory.read' },
  { path: 'finance', label: 'Finance', icon: 'finance', perm: 'finance.read' },
  { path: 'payroll', label: 'Employees & Payroll', icon: 'payroll', perm: 'employees.read' },
  { path: 'contracts', label: 'Contracts', icon: 'contracts', perm: 'contracts.read' },
  { path: 'procurement', label: 'Suppliers & Procurement', icon: 'procurement', perm: 'procurement.read' },
  { path: 'insights', label: 'Business Insights', icon: 'insights', perm: 'insights.read' },
  { path: 'automations', label: 'Automations', icon: 'automations', perm: 'automations.read' },
  { path: 'reports', label: 'Reports', icon: 'reports', perm: 'reports.read' },
  { sep: true },
  { path: 'approvals', label: 'Approvals', icon: 'approvals', perm: 'approvals.read' },
  { path: 'activity', label: 'Activity log', icon: 'audit', perm: 'audit.read' },
  { path: 'settings', label: 'Settings', icon: 'settings', perm: 'dashboard.read' },
];

const app = document.getElementById('app');
let cleanup = null;
let pendingCount = 0;

setUnauthorizedHandler(() => { setToken(null); state.me = null; renderAuth(); });

function setMe(me) {
  state.me = me;
  state.currency = me.business.currency;
}

async function boot() {
  if (!getToken()) return renderAuth();
  try {
    setMe(await api.get('/api/auth/me'));
    renderShell();
  } catch (e) {
    setToken(null);
    renderAuth();
  }
}

// ---------------------------------------------------------------- auth screens
function renderAuth(mode = 'login') {
  if (cleanup) { cleanup(); cleanup = null; }
  const depts = [['Inventory', 'Watches stock & forecasts stockouts'], ['Procurement', 'Compares suppliers, drafts orders'], ['Finance', 'Cash, invoices & forecasts'],
    ['HR & Payroll', 'Deterministic payroll prep'], ['Contracts', 'Renewals & notice deadlines'], ['Intelligence', 'Priorities across it all']];
  app.innerHTML = `<div class="auth">
    <div class="auth-side">
      <div class="brand"><div class="brand-mark">${logo()}</div><div><div class="brand-name" style="color:#fff">BusinessPilot</div><div class="brand-sub" style="color:#9aa6c0">Powered by Meta Muse Spark</div></div></div>
      <div>
        <h1>Your business, run by a team that never clocks out.</h1>
        <p style="color:#b8c2d8;max-width:46ch;margin-top:14px">Six specialist AI agents act as your inventory, purchasing, finance, payroll, contracts and analysis departments. They watch the numbers, prepare the work and wait for your approval.</p>
        <div class="depts">${depts.map(([a, b]) => `<div class="dept"><b>${a}</b>${b}</div>`).join('')}</div>
      </div>
      <div class="small" style="color:#7d8aa6">Built for the Meta Global AI Developer Hackathon. Demo data is fictional.</div>
    </div>
    <div class="auth-form"><div class="auth-card" id="authCard"></div></div>
  </div>`;
  const card = document.getElementById('authCard');
  if (mode === 'login') {
    card.innerHTML = `<h1 style="margin-bottom:6px">Sign in</h1><p class="muted">Use a demo account to explore Brew &amp; Bean Cafe.</p>
      <form id="loginForm" class="stack" style="gap:12px;margin-top:16px">
        <div class="field"><label for="em">Email</label><input class="input" id="em" name="email" type="email" required autocomplete="username"></div>
        <div class="field"><label for="pw">Password</label><input class="input" id="pw" name="password" type="password" required autocomplete="current-password"></div>
        <button class="btn primary" type="submit" style="justify-content:center;padding:10px">Sign in</button>
      </form>
      <div class="demo-accounts">${[['owner', 'Owner', 'Full access, approves spending'], ['manager', 'Manager', 'Operations, approves small orders'],
        ['hr', 'HR', 'Employees & payroll'], ['finance', 'Finance', 'Money, no salaries'], ['staff', 'Staff', 'Restricted view']].map(([u, r, d]) =>
        `<button class="demo-acc" data-demo="${u}@brewbean.demo" type="button"><span><b>${r}</b> <span class="muted">· ${d}</span></span><span class="muted small">${u}@brewbean.demo</span></button>`).join('')}</div>
      <p class="small muted" style="margin-top:10px">Demo password: <b>demo1234</b></p>
      <p style="margin-top:16px">New business? <a href="#" id="toRegister">Create an account</a></p>`;
    on(card, 'click', '[data-demo]', (e, el) => { card.querySelector('#em').value = el.dataset.demo; card.querySelector('#pw').value = 'demo1234'; card.querySelector('#loginForm').requestSubmit(); });
    card.querySelector('#toRegister').onclick = (e) => { e.preventDefault(); renderAuth('register'); };
    card.querySelector('#loginForm').onsubmit = async (e) => {
      e.preventDefault();
      const f = new FormData(e.target);
      try {
        const r = await api.post('/api/auth/login', { email: f.get('email'), password: f.get('password') });
        setToken(r.token); setMe(r);
        if (!location.hash || location.hash === '#/') location.hash = '#/overview';
        renderShell();
      } catch (err) { errToast(err); }
    };
  } else {
    card.innerHTML = `<h1 style="margin-bottom:6px">Create your business</h1><p class="muted">You become the owner. Add your own data, or start from a copy of the demo cafe.</p>
      <form id="regForm" class="stack" style="gap:12px;margin-top:16px">
        <div class="field"><label for="rn">Your name</label><input class="input" id="rn" name="name" required></div>
        <div class="field"><label for="re">Email</label><input class="input" id="re" name="email" type="email" required></div>
        <div class="field"><label for="rp">Password</label><input class="input" id="rp" name="password" type="password" minlength="8" required><span class="hint">At least 8 characters</span></div>
        <div class="field"><label for="rb">Business name</label><input class="input" id="rb" name="business_name" required></div>
        <div class="form-grid">
          <div class="field"><label for="rt">Business type</label><select class="input" id="rt" name="business_type">${['Cafe / Restaurant', 'Retail shop', 'E-commerce', 'Wholesale / Distribution', 'Services', 'Other'].map(x => `<option>${x}</option>`).join('')}</select></div>
          <div class="field"><label for="rc">Currency</label><input class="input" id="rc" name="currency" value="USD" maxlength="3" required></div>
        </div>
        <label class="check"><input type="checkbox" name="load_demo_data"> Start with a copy of the demo cafe's data</label>
        <button class="btn primary" type="submit" style="justify-content:center;padding:10px">Create business</button>
      </form><p style="margin-top:16px">Already have an account? <a href="#" id="toLogin">Sign in</a></p>`;
    card.querySelector('#toLogin').onclick = (e) => { e.preventDefault(); renderAuth('login'); };
    card.querySelector('#regForm').onsubmit = async (e) => {
      e.preventDefault();
      const f = new FormData(e.target);
      try {
        const r = await api.post('/api/auth/register', { name: f.get('name'), email: f.get('email'), password: f.get('password'),
          business_name: f.get('business_name'), business_type: f.get('business_type'), currency: String(f.get('currency')).toUpperCase(),
          load_demo_data: !!f.get('load_demo_data') });
        setToken(r.token); setMe(r); location.hash = '#/overview'; renderShell();
        toast('Business created', 'ok');
      } catch (err) { errToast(err); }
    };
  }
}

function logo() {
  return `<svg viewBox="0 0 32 32" width="22" height="22" aria-hidden="true"><path d="M9 22 L16 9 L23 22 L16 18 Z" fill="#3cc1b5"/></svg>`;
}

// ---------------------------------------------------------------- shell
function renderShell() {
  const me = state.me;
  const mock = me.ai.mode !== 'real';
  app.innerHTML = `<div class="shell">
    <aside class="sidebar" id="sidebar">
      <div class="brand"><div class="brand-mark">${logo()}</div><div><div class="brand-name">BusinessPilot</div><div class="brand-sub">Your AI operations team</div></div></div>
      <nav class="nav" id="nav" aria-label="Main">${PAGES.map(p => p.sep ? '<div class="nav-sep"></div>' : (can(p.perm) ?
        `<a href="#/${p.path}" data-path="${p.path}">${icon(p.icon)}<span>${p.label}</span>${p.path === 'approvals' ? '<span class="count hidden" id="navCount"></span>' : ''}</a>` : '')).join('')}</nav>
      <div class="sidebar-foot">
        <div><b style="color:var(--ink)">${esc(me.user.name)}</b></div>
        <div>${esc(me.role.charAt(0) + me.role.slice(1).toLowerCase())} · ${esc(me.business.name)}</div>
        <div style="margin-top:6px">${me.ai.mode === 'real' ? `Muse Spark <span class="muted">(${esc(me.ai.model)})</span>` : 'AI: mock mode'}</div>
      </div>
    </aside>
    <div class="main">
      <header class="topbar">
        <button class="icon-btn menu-btn" id="menuBtn" aria-label="Open menu">${icon('menu')}</button>
        <span class="biz">${esc(me.business.name)}</span>
        ${me.business.is_demo ? '<span class="badge outline">Demo data</span>' : ''}
        <span class="spacer"></span>
        ${me.memberships.length > 1 ? `<select class="input" id="bizSwitch" style="width:auto;padding:5px 8px" aria-label="Switch business">${me.memberships.map(m => `<option value="${m.id}" ${m.id === me.business.id ? 'selected' : ''}>${esc(m.name)}</option>`).join('')}</select>` : ''}
        <a class="btn teal sm" href="#/assistant">${icon('sparkle')} Ask BusinessPilot</a>
        ${can('approvals.read') ? `<a class="icon-btn" href="#/approvals" aria-label="Approvals">${icon('bell')}<span class="dot hidden" id="bellDot"></span></a>` : ''}
        <button class="icon-btn" id="themeBtn" aria-label="Toggle dark mode">${icon('moon')}</button>
        <button class="icon-btn" id="logoutBtn" aria-label="Sign out">${icon('logout')}</button>
      </header>
      ${mock ? `<div class="mock-banner">Mock AI mode — agents run deterministic templates over your live data. Add a Meta Model API key (MODEL_API_KEY) to switch on Muse Spark.</div>` : ''}
      <main class="content" id="content" tabindex="-1"></main>
    </div>
  </div>`;
  document.getElementById('menuBtn').onclick = () => document.getElementById('sidebar').classList.toggle('open');
  document.getElementById('logoutBtn').onclick = async () => { try { await api.post('/api/auth/logout'); } catch (e) { /* ignore */ } setToken(null); state.me = null; location.hash = ''; renderAuth(); };
  document.getElementById('themeBtn').onclick = () => {
    const root = document.documentElement;
    const next = root.dataset.theme === 'dark' ? 'light' : 'dark';
    root.dataset.theme = next;
    try { localStorage.setItem('bp_theme', next); } catch (e) { /* ignore */ }
  };
  const sw = document.getElementById('bizSwitch');
  if (sw) sw.onchange = async () => { try { const r = await api.post('/api/auth/switch', { business_id: Number(sw.value) }); setToken(r.token); setMe(r); renderShell(); } catch (e) { errToast(e); } };
  route();
  refreshCounts();
}

export async function refreshCounts() {
  if (!state.me || !can('approvals.read')) return;
  try {
    const r = await api.get('/api/approvals');
    pendingCount = r.approvals.filter(a => a.can_decide).length;
    const dot = document.getElementById('bellDot'), nc = document.getElementById('navCount');
    [dot, nc].forEach(el => { if (el) { el.textContent = pendingCount; el.classList.toggle('hidden', !pendingCount); } });
  } catch (e) { /* ignore */ }
}

export async function reloadMe() {
  setMe(await api.get('/api/auth/me'));
}

export function rerenderShell(me) { if (me) setMe(me); renderShell(); }

async function route() {
  if (!state.me) return;
  const [path, query] = (location.hash.replace(/^#\/?/, '') || 'overview').split('?');
  const page = PAGES.find(p => p.path === path && !p.sep) || PAGES[0];
  document.querySelectorAll('#nav a').forEach(a => a.classList.toggle('active', a.dataset.path === page.path));
  document.getElementById('sidebar')?.classList.remove('open');
  const content = document.getElementById('content');
  if (!content) return;
  if (cleanup) { try { cleanup(); } catch (e) { /* ignore */ } cleanup = null; }
  if (!can(page.perm)) { content.innerHTML = `<div class="empty"><h3>Not available for your role</h3><p>Ask the owner for access.</p></div>`; return; }
  content.innerHTML = '';
  document.title = `${page.label} · BusinessPilot`;
  try {
    const mod = await import(`./pages/${page.path}.js`);
    cleanup = await mod.render(content, new URLSearchParams(query || '')) || null;
  } catch (e) {
    console.error(e);
    content.innerHTML = `<div class="empty"><h3>This page couldn't load</h3><p>${esc(e.message || e)}</p><button class="btn" onclick="location.reload()">Reload</button></div>`;
  }
  content.focus({ preventScroll: true });
}

window.addEventListener('hashchange', route);

// global chart tooltips
const tip = document.createElement('div');
tip.className = 'chart-tip hidden';
document.body.appendChild(tip);
document.addEventListener('mousemove', (e) => {
  const t = e.target.closest && e.target.closest('[data-tip]');
  if (!t) { tip.classList.add('hidden'); return; }
  tip.textContent = t.getAttribute('data-tip');
  tip.classList.remove('hidden');
  const x = Math.min(e.clientX + 12, window.innerWidth - tip.offsetWidth - 8);
  tip.style.left = x + 'px';
  tip.style.top = (e.clientY - 34) + 'px';
});

setInterval(refreshCounts, 30000);
boot();
