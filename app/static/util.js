// Shared helpers: DOM, storage, translation, formatting, icons and the API (from Shrinkarr).
import { DICT } from './i18n.js';

export const $ = (s, el = document) => el.querySelector(s);
export const $$ = (s, el = document) => [...el.querySelectorAll(s)];
export const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
export const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } },
};

// --- language ------------------------------------------------------------------
let LANG = store.get('lang', null) || ((navigator.language || 'en').toLowerCase().startsWith('nl') ? 'nl' : 'en');
export const lang = () => LANG;
export function setLang(l) {
  LANG = DICT[l] ? l : 'en';
  store.set('lang', LANG);
  document.documentElement.lang = LANG;
  buildFormats();
}
/** t('key', {n: 3}) → text with {n} filled in. Keys ending in a count pick `key.one` when n === 1. */
export function t(key, vars = {}) {
  let s = DICT[LANG][key] ?? DICT.en[key];
  if (vars.n === 1 && (DICT[LANG][key + '.one'] ?? DICT.en[key + '.one'])) s = DICT[LANG][key + '.one'] ?? DICT.en[key + '.one'];
  if (s == null) return key;
  return s.replace(/\{(\w+)\}/g, (_, k) => (vars[k] ?? `{${k}}`));
}

// --- formatting ---------------------------------------------------------------
let nf0, nf1, nf2, LOCALE;
function buildFormats() {
  LOCALE = LANG === 'nl' ? 'nl-NL' : 'en-GB';
  nf0 = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 0 });
  nf1 = new Intl.NumberFormat(LOCALE, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  nf2 = new Intl.NumberFormat(LOCALE, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
setLang(LANG);
export const num = (v, d = 0) => (d === 2 ? nf2 : d === 1 ? nf1 : nf0).format(v);
export function bytes(b) {
  if (b == null || isNaN(b)) return '–';
  const a = Math.abs(b);
  if (a >= 1e12) return nf2.format(b / 1e12) + ' TB';
  if (a >= 1e9) return nf1.format(b / 1e9) + ' GB';
  if (a >= 1e6) return nf0.format(b / 1e6) + ' MB';
  return nf0.format(b / 1e3) + ' kB';
}
export function dur(s) {
  if (s == null || isNaN(s)) return '–';
  s = Math.max(0, Math.round(s));
  const h_ = t('unit.h'), d_ = t('unit.d');
  if (s < 60) return '<1 min';
  const m = Math.round(s / 60);
  if (m < 60) return m + ' min';
  const h = Math.floor(m / 60), mm = m % 60;
  if (h < 48) return `${h} ${h_}` + (mm ? ` ${mm} min` : '');
  const d = Math.floor(h / 24), hh = h % 24;
  return `${d} ${d_}` + (hh ? ` ${hh} ${h_}` : '');
}
export const when = (ts) => (ts ? new Date(ts * 1000).toLocaleString(LOCALE, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : '');
export const clock = (ts) => new Date(ts * 1000).toLocaleTimeString(LOCALE, { hour: '2-digit', minute: '2-digit' });
export const collate = (a, b) => a.localeCompare(b, LOCALE);

// --- icons (lucide style) -----------------------------------------------------
const ic = (p, extra = '') => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" ${extra}>${p}</svg>`;
export const I = {
  search: ic('<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>'),
  sun: ic('<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>'),
  moon: ic('<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>'),
  auto: ic('<circle cx="12" cy="12" r="9"/><path d="M12 3v18" /><path d="M12 3a9 9 0 0 1 0 18z" fill="currentColor"/>'),
  check: ic('<path d="M20 6 9 17l-5-5"/>', 'stroke-width="3"'),
  x: ic('<path d="M18 6 6 18M6 6l12 12"/>'),
  download: ic('<path d="M12 3v12M7 10l5 5 5-5"/><path d="M5 21h14"/>'),
  server: ic('<rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="13" width="18" height="7" rx="2"/><path d="M7 7.5h.01M7 16.5h.01"/>'),
  tv: ic('<rect x="2" y="6" width="20" height="14" rx="2"/><path d="m8 2 4 4 4-4"/>'),
  activity: ic('<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>'),
  eye: ic('<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>'),
  play: ic('<path d="m6 3 14 9-14 9z"/>'),
  pause: ic('<rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/>'),
  up: ic('<path d="m18 15-6-6-6 6"/>'),
  trash: ic('<path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/>'),
  retry: ic('<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/>'),
  refresh: ic('<path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/>'),
  select: ic('<rect x="3" y="3" width="18" height="18" rx="4"/><path d="m8 12 3 3 5-6"/>'),
  chev: ic('<path d="m9 18 6-6-6-6"/>'),
  chevDown: ic('<path d="m6 9 6 6 6-6"/>', 'stroke-width="2.4"'),
  back: ic('<path d="m15 18-6-6 6-6"/>'),
  film: ic('<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M7 3v18M17 3v18M3 7.5h4M3 12h18M3 16.5h4M17 7.5h4M17 16.5h4"/>'),
  inbox: ic('<path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.5 5.1 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.5-6.9A2 2 0 0 0 16.8 4H7.2a2 2 0 0 0-1.7 1.1z"/>'),
  clock: ic('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>'),
  flask: ic('<path d="M9 3h6M10 3v6L4.5 18.5A2 2 0 0 0 6.2 21h11.6a2 2 0 0 0 1.7-2.5L14 9V3"/><path d="M7 15h10"/>'),
  plus: ic('<path d="M12 5v14M5 12h14"/>'),
  lock: ic('<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>'),
  logout: ic('<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="m16 17 5-5-5-5"/><path d="M21 12H9"/>'),
  link: ic('<path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7"/><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/>'),
  copy: ic('<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/>'),
  globe: ic('<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>'),
};
export const icon = (name, size) => (size ? I[name].replace('<svg', `<svg width="${size}" height="${size}"`) : I[name]);

// --- API and messages ----------------------------------------------------------
export const events = new EventTarget();
export async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin', ...opts });
  if (r.status === 401 && !path.startsWith('/api/auth/')) {
    events.dispatchEvent(new Event('login'));
    throw new Error(t('login.required'));
  }
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch { /* not json */ }
    const err = new Error(t('err.' + msg) !== 'err.' + msg ? t('err.' + msg) : msg);
    err.status = r.status; err.code = msg;
    throw err;
  }
  return r.json();
}
export const put = (path, body) => api(path, { method: 'PUT', body: JSON.stringify(body) });
export const post = (path, body = {}) => api(path, { method: 'POST', body: JSON.stringify(body) });

export function toast(msg, kind = '') {
  const el = document.createElement('div');
  el.className = 'toast ' + kind;
  el.textContent = msg;
  $('#toasts').append(el);
  setTimeout(() => el.remove(), 4500);
}
