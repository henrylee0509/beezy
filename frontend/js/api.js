// Thin fetch wrapper: bearer token, JSON, consistent errors.
const KEY = 'bp_token';
let token = null;
try { token = localStorage.getItem(KEY); } catch (e) { /* storage unavailable */ }

export function setToken(t) {
  token = t;
  try { t ? localStorage.setItem(KEY, t) : localStorage.removeItem(KEY); } catch (e) { /* ignore */ }
}
export function getToken() { return token; }

export class ApiError extends Error {
  constructor(status, message, code, details) { super(message); this.status = status; this.code = code; this.details = details; }
}

let onUnauthorized = () => {};
export function setUnauthorizedHandler(fn) { onUnauthorized = fn; }

async function request(method, path, body, opts = {}) {
  const headers = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  let payload = body;
  if (body && !(body instanceof FormData)) { headers['Content-Type'] = 'application/json'; payload = JSON.stringify(body); }
  const res = await fetch(path, { method, headers, body: payload });
  if (opts.raw) {
    if (!res.ok) throw new ApiError(res.status, `Request failed (${res.status})`);
    return res;
  }
  let data = null;
  try { data = await res.json(); } catch (e) { data = null; }
  if (!res.ok) {
    const err = data && data.error ? data.error : {};
    if (res.status === 401 && path !== '/api/auth/login') onUnauthorized();
    let msg = err.message || `Request failed (${res.status})`;
    if (err.details && Array.isArray(err.details)) msg += ': ' + err.details.map(d => `${(d.loc || []).join('.')} ${d.msg}`).join('; ');
    throw new ApiError(res.status, msg, err.code, err.details);
  }
  return data;
}

export const api = {
  get: (p) => request('GET', p),
  post: (p, b) => request('POST', p, b || {}),
  put: (p, b) => request('PUT', p, b || {}),
  del: (p) => request('DELETE', p),
  upload: (p, form) => request('POST', p, form),
  raw: (p) => request('GET', p, null, { raw: true }),
};

export async function download(path, fallbackName) {
  const res = await api.raw(path);
  const blob = await res.blob();
  const cd = res.headers.get('Content-Disposition') || '';
  const m = cd.match(/filename="([^"]+)"/);
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = m ? m[1] : fallbackName;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500);
}
