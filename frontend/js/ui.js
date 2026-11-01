// UI helpers: escaping, formatting, icons, modal, toast, markdown, small components.
export const esc = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

export const state = { me: null, currency: 'USD' };

export const fmt = {
  money(n, opts = {}) {
    if (n === null || n === undefined || Number.isNaN(n)) return '—';
    const v = Number(n);
    const s = Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: opts.dp ?? 2, maximumFractionDigits: opts.dp ?? 2 });
    return `${v < 0 ? '−' : ''}${opts.noCur ? '' : state.currency + ' '}${s}`;
  },
  compact(n) {
    if (n === null || n === undefined) return '—';
    const v = Number(n), a = Math.abs(v);
    const s = a >= 1e6 ? (a / 1e6).toFixed(1) + 'M' : a >= 1e3 ? (a / 1e3).toFixed(a >= 1e4 ? 0 : 1) + 'k' : a.toFixed(0);
    return (v < 0 ? '−' : '') + s;
  },
  num(n, dp = 0) { return n === null || n === undefined ? '—' : Number(n).toLocaleString(undefined, { maximumFractionDigits: dp, minimumFractionDigits: 0 }); },
  pct(n, signed = false) { if (n === null || n === undefined) return '—'; return (signed && n > 0 ? '+' : '') + Number(n).toFixed(1) + '%'; },
  date(s) {
    if (!s) return '—';
    const d = new Date(String(s).length <= 10 ? s + 'T00:00:00' : s.replace(' ', 'T'));
    if (Number.isNaN(d.getTime())) return s;
    return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: d.getFullYear() !== new Date().getFullYear() ? 'numeric' : undefined });
  },
  datetime(s) {
    if (!s) return '—';
    const d = new Date(s.replace(' ', 'T'));
    return Number.isNaN(d.getTime()) ? s : d.toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
  },
  rel(days) { if (days === null || days === undefined) return '—'; if (days === 0) return 'today'; return days > 0 ? `in ${days} day${days === 1 ? '' : 's'}` : `${-days} day${days === -1 ? '' : 's'} ago`; },
  status(s) { return String(s || '').replace(/_/g, ' ').toLowerCase().replace(/^\w/, c => c.toUpperCase()); },
};

const P = {
  overview: '<path d="M3 13h8V3H3zM13 21h8V11h-8zM3 21h8v-6H3zM13 3v6h8V3z"/>',
  assistant: '<path d="M12 3a7 7 0 0 0-7 7v3l-2 3h18l-2-3v-3a7 7 0 0 0-7-7z"/><path d="M9.5 19a2.5 2.5 0 0 0 5 0"/>',
  inventory: '<path d="M21 8 12 3 3 8v8l9 5 9-5z"/><path d="M3 8l9 5 9-5M12 13v8"/>',
  finance: '<path d="M3 17l6-6 4 4 8-8"/><path d="M15 7h6v6"/>',
  payroll: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M17 11h4M19 9v4"/>',
  contracts: '<path d="M7 3h7l5 5v13H7z"/><path d="M14 3v5h5M10 13h6M10 17h6"/>',
  procurement: '<path d="M3 4h2l2.4 11h11L21 7H6.2"/><circle cx="9" cy="20" r="1.5"/><circle cx="18" cy="20" r="1.5"/>',
  insights: '<path d="M9 18h6M10 21h4"/><path d="M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2V16h5v-.1c0-.8.4-1.5 1-2A6 6 0 0 0 12 3z"/>',
  automations: '<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>',
  reports: '<path d="M5 3h14v18H5z"/><path d="M9 17v-4M12 17V9M15 17v-6"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-1.8-.3 1.6 1.6 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.6 1.6 0 0 0-1-1.5 1.6 1.6 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0 .3-1.8 1.6 1.6 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.6 1.6 0 0 0 1.5-1 1.6 1.6 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 1.8.3H9a1.6 1.6 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.6 1.6 0 0 0 1 1.5 1.6 1.6 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0-.3 1.8V9a1.6 1.6 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.6 1.6 0 0 0-1.5 1z"/>',
  approvals: '<path d="M9 11l3 3 8-8"/><path d="M20 12v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h9"/>',
  audit: '<path d="M12 8v4l3 2"/><circle cx="12" cy="12" r="9"/>',
  bell: '<path d="M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.7 21a2 2 0 0 1-3.4 0"/>',
  mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>',
  send: '<path d="M4 12 20 4l-6 16-3-7z"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  x: '<path d="M6 6l12 12M18 6 6 18"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/>',
  sparkle: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/>',
  download: '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>',
  upload: '<path d="M12 20V9M7 14l5-5 5 5M5 4h14"/>',
  logout: '<path d="M15 4h4v16h-4M10 17l5-5-5-5M15 12H3"/>',
  moon: '<path d="M21 13A9 9 0 1 1 11 3a7 7 0 0 0 10 10z"/>',
  check: '<path d="M5 12l5 5L20 7"/>',
  shield: '<path d="M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6z"/>',
  bi: '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3M8 13l2-2 2 2 2-3"/>',
};
export const icon = (name, cls = '') => `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${P[name] || ''}</svg>`;

export function toast(msg, kind = '') {
  const el = document.createElement('div');
  el.className = `toast ${kind}`;
  el.textContent = msg;
  document.getElementById('toasts').appendChild(el);
  setTimeout(() => el.remove(), kind === 'error' ? 6000 : 3500);
}

export function errToast(e) { toast(e && e.message ? e.message : String(e), 'error'); }

// Modal: returns a promise resolving with form data (object) or null when cancelled.
export function modal({ title, body, submit = 'Save', wide = false, onOpen, danger = false, cancel = 'Cancel', noFooter = false }) {
  return new Promise((resolve) => {
    const back = document.createElement('div');
    back.className = 'modal-back';
    back.innerHTML = `<form class="modal ${wide ? 'wide' : ''}" role="dialog" aria-modal="true" aria-label="${esc(title)}">
      <div class="modal-head"><h2>${esc(title)}</h2><button type="button" class="icon-btn x" data-close aria-label="Close">${icon('x')}</button></div>
      <div class="modal-body">${body}</div>
      ${noFooter ? '' : `<div class="modal-foot"><button type="button" class="btn" data-close>${esc(cancel)}</button>
        ${submit ? `<button type="submit" class="btn ${danger ? 'danger' : 'primary'}">${esc(submit)}</button>` : ''}</div>`}
    </form>`;
    const form = back.querySelector('form');
    const close = (v) => { back.remove(); document.removeEventListener('keydown', onKey); resolve(v); };
    const onKey = (e) => { if (e.key === 'Escape') close(null); };
    document.addEventListener('keydown', onKey);
    back.addEventListener('click', (e) => { if (e.target === back || e.target.closest('[data-close]')) close(null); });
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      const fd = new FormData(form);
      const out = {};
      for (const [k, v] of fd.entries()) out[k] = v;
      form.querySelectorAll('input[type=checkbox]').forEach(cb => { out[cb.name] = cb.checked; });
      close(out);
    });
    document.body.appendChild(back);
    const first = form.querySelector('input, select, textarea');
    if (first) first.focus();
    if (onOpen) onOpen(form, close);
  });
}

export async function confirmBox(title, text, submit = 'Confirm', danger = false) {
  const r = await modal({ title, body: `<p>${esc(text)}</p>`, submit, danger });
  return r !== null;
}

// Minimal, safe markdown: escape first, then format.
export function md(src) {
  const lines = esc(src || '').split('\n');
  let out = '', list = null, para = [];
  const inline = (s) => s
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>')
    .replace(/(^|\s)_([^_\n]+)_(?=\s|$|[.,;])/g, '$1<em>$2</em>')
    .replace(/\[([^\]]+)\]\((#\/[^)\s]+)\)/g, '<a href="$2">$1</a>');
  const flushPara = () => { if (para.length) { out += `<p>${inline(para.join(' '))}</p>`; para = []; } };
  const flushList = () => { if (list) { out += `</${list}>`; list = null; } };
  for (const raw of lines) {
    const l = raw.trimEnd();
    let m;
    if (!l.trim()) { flushPara(); flushList(); continue; }
    if ((m = l.match(/^#{1,4}\s+(.*)/))) { flushPara(); flushList(); out += `<h4>${inline(m[1])}</h4>`; continue; }
    if ((m = l.match(/^&gt;\s?(.*)/))) { flushPara(); flushList(); out += `<blockquote>${inline(m[1])}</blockquote>`; continue; }
    if ((m = l.match(/^\s*[-*•]\s+(.*)/))) { flushPara(); if (list !== 'ul') { flushList(); out += '<ul>'; list = 'ul'; } out += `<li>${inline(m[1])}</li>`; continue; }
    if ((m = l.match(/^\s*\d+[.)]\s+(.*)/))) { flushPara(); if (list !== 'ol') { flushList(); out += '<ol>'; list = 'ol'; } out += `<li>${inline(m[1])}</li>`; continue; }
    if (/^\|.*\|$/.test(l.trim())) {
      flushPara(); flushList();
      const cells = l.trim().slice(1, -1).split('|').map(c => c.trim());
      if (cells.every(c => /^:?-{2,}:?$/.test(c))) continue;
      out += `<div class="small">${cells.map(inline).join(' &nbsp;|&nbsp; ')}</div>`;
      continue;
    }
    flushList();
    para.push(l.trim());
  }
  flushPara(); flushList();
  return out;
}

export function statusBadge(status) {
  const s = String(status || '').toUpperCase();
  const map = {
    HEALTHY: 'green', LOW: 'amber', URGENT: 'red', CRITICAL: 'red', OUT: 'red',
    DRAFT: '', AWAITING_APPROVAL: 'amber', APPROVED: 'blue', SUBMITTED: 'blue', PARTIALLY_RECEIVED: 'teal', COMPLETED: 'green', CANCELLED: '',
    PENDING: 'amber', ACCEPTED: 'blue', REJECTED: '', PAID: 'green', OPEN: 'amber', ACTIVE: 'green', INACTIVE: '',
    HIGH: 'red', MEDIUM: 'amber', TRIGGERED: 'teal', NO_ACTION: '', FAILED: 'red', RUNNING: 'blue',
  };
  const labels = { LOW: 'Low stock', OUT: 'Out of stock', AWAITING_APPROVAL: 'Awaiting approval', NO_ACTION: 'No action' };
  return `<span class="badge ${map[s] ?? ''}">${esc(labels[s] || fmt.status(s))}</span>`;
}

export function meter(pct, level, mark) {
  const color = { HEALTHY: 'var(--green)', LOW: 'var(--amber)', URGENT: 'var(--red)', CRITICAL: 'var(--red)', OUT: 'var(--red)' }[level] || 'var(--blue)';
  return `<div class="meter" title="${fmt.pct(pct)}"><span style="width:${Math.max(2, Math.min(100, pct))}%;background:${color}"></span>${mark ? `<i class="mark" style="left:${mark}%"></i>` : ''}</div>`;
}

export function empty(title, text, action = '') {
  return `<div class="empty"><h3>${esc(title)}</h3><p>${esc(text)}</p>${action}</div>`;
}

export function skeleton(lines = 4) {
  return `<div class="stack" style="gap:10px">${Array.from({ length: lines }, (_, i) => `<div class="skel" style="width:${90 - i * 12}%"></div>`).join('')}</div>`;
}

export function aiSource(source, verification) {
  if (!source && !verification) return '';
  let v = '';
  if (verification && verification.figures_checked) {
    const ok = verification.status === 'verified';
    v = `<span class="badge ${ok ? 'green' : 'amber'} verify" title="${ok ? 'Every figure was found in a tool result' : 'Unverified: ' + esc((verification.unverified || []).join(', '))}">
      ${icon('shield')} ${verification.figures_verified}/${verification.figures_checked} figures verified</span>`;
  }
  return `<div class="ai-source">${source ? `<span>${icon('sparkle')} ${esc(source)}</span>` : ''}${v}</div>`;
}

export function on(root, event, selector, handler) {
  root.addEventListener(event, (e) => {
    const el = e.target.closest(selector);
    if (el && root.contains(el)) handler(e, el);
  });
}

export const can = (perm) => !!(state.me && state.me.permissions.includes(perm));

export function field(name, label, { type = 'text', value = '', hint = '', options = null, required = false, step, full = false, attrs = '' } = {}) {
  let input;
  if (options) {
    input = `<select class="input" name="${name}" id="f_${name}" ${required ? 'required' : ''} ${attrs}>${options.map(o => {
      const [v, l] = Array.isArray(o) ? o : [o, o];
      return `<option value="${esc(v)}" ${String(v) === String(value) ? 'selected' : ''}>${esc(l)}</option>`;
    }).join('')}</select>`;
  } else if (type === 'textarea') {
    input = `<textarea class="input" name="${name}" id="f_${name}" rows="3" ${attrs}>${esc(value)}</textarea>`;
  } else if (type === 'checkbox') {
    return `<label class="check ${full ? 'full' : ''}"><input type="checkbox" name="${name}" ${value ? 'checked' : ''} ${attrs}> ${esc(label)}</label>`;
  } else {
    input = `<input class="input" type="${type}" name="${name}" id="f_${name}" value="${esc(value)}" ${required ? 'required' : ''} ${step ? `step="${step}"` : ''} ${attrs}>`;
  }
  return `<div class="field ${full ? 'full' : ''}"><label for="f_${name}">${esc(label)}</label>${input}${hint ? `<span class="hint">${esc(hint)}</span>` : ''}</div>`;
}
