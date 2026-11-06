// PlexArr web UI (no build step): the friends' library, detail panel, downloads, activity, login and routing.
import { $, $$, esc, store, t, bytes, dur, num, when, collate, I, api, post, toast, events, lang } from './util.js';
import { renderSettings } from './settings.js';
import { renderWizard } from './wizard.js';

export const S = {
  route: 'library',
  lib: null,
  status: null,
  dl: null,
  f: { q: '', type: 'all', show: 'all', sort: 'added', ...store.get('filters', {}) },
  detail: null,
  auth: { enabled: false, loggedIn: true },
  setupDone: true,
};
const poster = (it, w = 300) => `/api/poster/${it.server}/${it.key}?w=${w}`;
const serverName = (id) => (S.status?.servers || []).find((s) => s.id === id)?.name || '';

// --- static texts and theme ------------------------------------------------------------------
export function applyStaticTexts() {
  $$('[data-i18n]').forEach((el) => { el.textContent = t(el.dataset.i18n); });
  $$('[data-i18n-title]').forEach((el) => { el.title = t(el.dataset.i18nTitle); el.setAttribute('aria-label', t(el.dataset.i18nTitle)); });
  applyTheme();
}
function applyTheme() {
  const th = store.get('theme', 'auto');
  if (th === 'auto') delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = th;
  $('#theme-btn').innerHTML = th === 'light' ? I.sun : th === 'dark' ? I.moon : I.auto;
  $('#theme-btn').title = t('theme.' + th);
  $('#theme-btn').setAttribute('aria-label', t('theme.' + th));
}
$('#theme-btn').addEventListener('click', () => {
  const order = ['auto', 'light', 'dark'];
  store.set('theme', order[(order.indexOf(store.get('theme', 'auto')) + 1) % 3]);
  applyTheme();
});
$('#logout-btn').addEventListener('click', async () => { await post('/api/auth/logout'); location.reload(); });

// --- routing -------------------------------------------------------------------------------------
const ROUTES = { downloads: 'downloads', activity: 'activity', activiteit: 'activity', settings: 'settings', instellingen: 'settings', setup: 'setup' };
function parseHash() {
  const parts = location.hash.replace(/^#\/?/, '').split('/');
  if (parts[0] === 'item') return { route: 'library', item: { kind: parts[1], server: parts[2], key: parts[3] } };
  return { route: ROUTES[parts[0]] || 'library' };
}
export async function route(force = false) {
  const { route: r, item } = parseHash();
  if (!S.setupDone && r !== 'setup') { location.hash = '#/setup'; return; }
  document.body.classList.toggle('setup-mode', r === 'setup');
  const changed = force || r !== S.route || !$('#view').dataset.route;
  S.route = r;
  $$('#tabs a').forEach((a) => a.classList.toggle('active', a.dataset.route === r));
  if (changed) {
    $('#view').dataset.route = r;
    const views = { library: renderLibrary, downloads: renderDownloads, activity: renderActivity, settings: () => renderSettings(S), setup: () => renderWizard(S) };
    await views[r]();
    window.scrollTo(0, 0);
  }
  if (item) openDetail(item.kind, item.server, item.key); else closeDrawer(false);
}
window.addEventListener('hashchange', () => route());

// ==================================================================================================
// Library
// ==================================================================================================
const RES_RANK = { '480p': 1, '576p': 2, '720p': 3, '1080p': 4, '2160p': 5 };
const resLabel = (r) => (r === '2160p' ? '4K' : r || '?');

function mineBadge(it) {
  const m = it.mine || {};
  if (it.kind === 'movie') {
    if (m.state === 'have') return it.better ? `<span class="badge accent">${t('mine.better')}</span>` : `<span class="badge good">${t('mine.have')}</span>`;
    if (m.state === 'missing') return `<span class="badge warn">${t('mine.missing')}</span>`;
    return '';
  }
  if (m.state === 'have') return `<span class="badge good">${t('mine.have')}</span>`;
  if (m.state === 'partial' || m.state === 'missing') return `<span class="badge warn">${t('mine.lacking', { n: it.lacking })}</span>`;
  return '';
}

function matches(it) {
  const f = S.f;
  if (f.type !== 'all' && it.kind !== f.type) return false;
  if (f.q) {
    const q = f.q.toLowerCase();
    if (!it.title.toLowerCase().includes(q) && String(it.year || '') !== q) return false;
  }
  const st = it.mine?.state || 'none';
  switch (f.show) {
    case 'lacking': return it.kind === 'movie' ? st === 'missing' : (it.lacking || 0) > 0;
    case 'better': return !!it.better;
    case 'none': return st === 'none';
    case 'have': return st === 'have';
    case '4k': return it.res === '2160p';
    default: return true;
  }
}
function sorted(items) {
  const k = S.f.sort;
  const by = {
    added: (a, b) => b.added - a.added,
    title: (a, b) => collate(a.title, b.title),
    size: (a, b) => b.size - a.size,
    year: (a, b) => (b.year || 0) - (a.year || 0),
  }[k] || ((a, b) => b.added - a.added);
  return [...items].sort(by);
}
const saveFilters = () => store.set('filters', { type: S.f.type, show: S.f.show, sort: S.f.sort });

export async function loadLibrary() { S.lib = await api('/api/library'); }

async function renderLibrary() {
  const v = $('#view');
  if (!S.lib) {
    v.innerHTML = `<div class="empty"><div class="spinner"></div><p>${t('lib.loading')}</p></div>`;
    try { await loadLibrary(); } catch (e) { v.innerHTML = `<div class="empty">${t('lib.loadFailed', { err: esc(e.message) })}</div>`; return; }
  }
  if (!S.lib.configured) {
    v.innerHTML = `<div class="empty">${I.server}<p>${t('lib.notConfigured')}</p><a class="btn primary" href="#/setup">${t('lib.openSetup')}</a></div>`;
    return;
  }
  const f = S.f;
  v.innerHTML = `
    <div id="lib-banners"></div>
    <div class="lib-head">
      <section class="ringcard" id="lib-stats"></section>
      <div class="lib-controls">
        <div class="lib-search">
          <label class="field">${I.search}<input id="q" type="search" placeholder="${t('lib.search')}" aria-label="${t('lib.search')}" value="${esc(f.q)}" autocomplete="off"></label>
          <button class="icon-btn lg" id="refresh-btn" aria-label="${t('lib.refresh')}" title="${t('lib.refresh')}">${I.refresh}</button>
        </div>
        <div class="seg seg-full" id="type-seg">
          ${[['all', t('lib.all')], ['movie', t('lib.movies')], ['show', t('lib.series')]].map(([k, l]) => `<button data-v="${k}" class="${f.type === k ? 'on' : ''}">${l}</button>`).join('')}
        </div>
        <div class="chips" id="chips"></div>
      </div>
    </div>
    <div class="grid" id="grid"></div>`;
  renderBanners();
  renderStats();
  renderChips();
  updateGrid();

  let tm;
  $('#q').addEventListener('input', (e) => { clearTimeout(tm); tm = setTimeout(() => { f.q = e.target.value; updateGrid(); }, 120); });
  $('#type-seg').addEventListener('click', (e) => {
    const b = e.target.closest('button'); if (!b) return;
    f.type = b.dataset.v; $$('#type-seg button').forEach((x) => x.classList.toggle('on', x === b)); saveFilters(); updateGrid();
  });
  const setChip = (k, v) => { f[k] = v; saveFilters(); renderChips(); updateGrid(); };
  $('#chips').addEventListener('change', (e) => { if (e.target.dataset.k) setChip(e.target.dataset.k, e.target.value); });
  $('#chips').addEventListener('click', (e) => {
    const chip = e.target.closest('button.chip');
    if (chip) { toggleChipMenu(chip); return; }
    const o = e.target.closest('.chip-menu [role="option"]');
    if (o) {
      const k = o.dataset.k;
      closeChipMenu(); setChip(k, o.dataset.v);
      $(`#chips .chip[data-k="${k}"]`)?.focus();
      return;
    }
    const b = e.target.closest('.chip-x'); if (!b) return;
    setChip(b.dataset.k, 'all');
  });
  $('#chips').addEventListener('keydown', (e) => {
    const menu = e.target.closest('.chip-menu');
    if (e.key === 'ArrowDown' && e.target.matches('button.chip')) { e.preventDefault(); toggleChipMenu(e.target, true); return; }
    if (!menu) return;
    const items = [...menu.querySelectorAll('[role="option"]')], i = items.indexOf(e.target);
    const go = { ArrowDown: i + 1, ArrowUp: i - 1, Home: 0, End: items.length - 1 }[e.key];
    if (go !== undefined) { e.preventDefault(); items[(go + items.length) % items.length].focus(); }
    else if (e.key === 'Escape') { e.stopPropagation(); closeChipMenu(true); }
    else if (e.key === 'Tab') closeChipMenu();
  });
  $('#refresh-btn').addEventListener('click', async () => {
    const b = $('#refresh-btn'); b.disabled = true; b.innerHTML = '<span class="spinner"></span>';
    await post('/api/library/refresh');
    toast(t('lib.refreshing'));
    setTimeout(async () => { await loadLibrary(); if (S.route === 'library') renderLibrary(); }, 15000);
  });
  $('#grid').addEventListener('click', (e) => {
    const tile = e.target.closest('.tile'); if (!tile) return;
    location.hash = `#/item/${tile.dataset.kind}/${tile.dataset.server}/${tile.dataset.key}`;
  });
  $('#grid').addEventListener('keydown', (e) => { if (e.key === 'Enter') e.target.closest('.tile')?.click(); });
}

function renderBanners() {
  const st = S.status;
  if (!st) return;
  const out = [];
  for (const s of st.servers.filter((x) => x.error)) out.push(`<div class="banner warn">${t('lib.serverError', { name: esc(s.name), err: esc(s.error) })}</div>`);
  const apps = ['radarr', 'sonarr'].filter((a) => st[a]);
  const inactive = apps.filter((a) => !(st.links[a] || {}).active);
  if (apps.length && inactive.length) {
    out.push(`<div class="banner info">${t('lib.inactive', { apps: inactive.map((a) => a[0].toUpperCase() + a.slice(1)).join(' & ') })}<a class="btn sm" href="#/settings">${t('lib.openSettings')}</a></div>`);
  }
  $('#lib-banners').innerHTML = out.join('');
}

function renderStats() {
  const items = S.lib.items;
  const movies = items.filter((i) => i.kind === 'movie'), shows = items.filter((i) => i.kind === 'show');
  const total = items.reduce((a, i) => a + i.size, 0);
  const lacking = movies.filter((m) => m.mine.state === 'missing').length + shows.filter((s) => (s.lacking || 0) > 0).length;
  const better = movies.filter((m) => m.better).length;
  const none = items.filter((i) => i.mine.state === 'none').length;
  const servers = S.lib.servers.map((s) => esc(s.name)).join(', ');
  $('#lib-stats').innerHTML = `
    <div class="ring-row">
      <div class="ring-nums">
        <div class="legend">${I.server} ${t('lib.from', { names: servers })}</div>
        <div class="big">${num(movies.length)} ${t('lib.moviesLc')} · ${num(shows.length)} ${t('lib.seriesLc')}</div>
        <div class="val">${bytes(total)}${S.lib.indexedAt ? ` · ${t('lib.indexed', { when: when(S.lib.indexedAt) })}` : ''}</div>
      </div>
    </div>
    <div class="ring-strip">
      <div><div class="l">${t('lib.lacking')}</div><div class="v">${num(lacking)}</div></div>
      <div><div class="l">${t('lib.better')}</div><div class="v">${num(better)}</div></div>
      <div><div class="l">${t('lib.notInArr')}</div><div class="v">${num(none)}</div></div>
    </div>`;
}

// With a mouse, filter chips open their own menu instead of a native <select>, whose open list can't be styled.
function closeChipMenu(refocus = false) {
  const menu = $('.chip-menu:not([hidden])'); if (!menu) return;
  const chip = menu.previousElementSibling;
  menu.hidden = true; chip.setAttribute('aria-expanded', 'false');
  if (refocus) chip.focus();
}

function toggleChipMenu(chip, open) {
  const menu = chip.nextElementSibling, wasOpen = !menu.hidden;
  closeChipMenu();
  if (wasOpen && !open) return;
  menu.hidden = false; chip.setAttribute('aria-expanded', 'true');
  // Align the menu right when it would stick out of the screen.
  menu.classList.remove('end');
  if (menu.getBoundingClientRect().right > document.documentElement.clientWidth - 8) menu.classList.add('end');
  (menu.querySelector('[aria-selected="true"]') || menu.firstElementChild).focus();
}

document.addEventListener('pointerdown', (e) => { if (!e.target.closest('.chip-group')) closeChipMenu(); });

function renderChips() {
  const f = S.f;
  // Touch screens keep the native picker (the iOS wheel); with a mouse the open list of a <select> looks out of place.
  const native = matchMedia('(pointer: coarse)').matches;
  const chip = (k, title, opts, value, active) => {
    const cur = (opts.find(([v]) => v === value) || opts[0])[1];
    const pick = native
      ? `<label class="chip" title="${esc(title)}"><span>${esc(cur)}</span>${I.chevDown}
        <select data-k="${k}" aria-label="${esc(title)}">${opts.map(([v, l]) => `<option value="${esc(v)}" ${v === value ? 'selected' : ''}>${esc(l)}</option>`).join('')}</select></label>`
      : `<button class="chip" data-k="${k}" title="${esc(title)}" aria-label="${esc(title)}: ${esc(cur)}" aria-haspopup="listbox" aria-expanded="false"><span>${esc(cur)}</span>${I.chevDown}</button>
      <div class="chip-menu" role="listbox" aria-label="${esc(title)}" hidden>${opts.map(([v, l]) => `<button role="option" data-k="${k}" data-v="${esc(v)}" aria-selected="${v === value}">${esc(l)}${I.check}</button>`).join('')}</div>`;
    return `<span class="chip-group ${active ? 'on' : ''}">
      ${pick}
      ${active ? `<button class="chip-x" data-k="${k}" aria-label="${esc(t('chip.remove'))}">${I.x}</button>` : ''}</span>`;
  };
  $('#chips').innerHTML = [
    chip('show', t('chip.show'), ['all', 'lacking', 'better', '4k', 'none', 'have'].map((k) => [k, t('show.' + k)]), f.show, f.show !== 'all'),
    chip('sort', t('chip.sort'), ['added', 'title', 'year', 'size'].map((k) => [k, t('sort.' + k)]), f.sort, false),
  ].join('');
}

let gridItems = [], gridShown = 0;
function updateGrid() {
  gridItems = sorted(S.lib.items.filter(matches));
  gridShown = 0;
  $('#grid').innerHTML = gridItems.length ? '' : `<div class="empty" style="grid-column:1/-1">${I.film}<p>${t('lib.none')}</p></div>`;
  moreTiles();
}
function tile(it) {
  const sub = it.kind === 'movie' ? (it.year || '') : t('lib.episodes', { n: it.episodes });
  return `<div class="tile" tabindex="0" data-kind="${it.kind}" data-server="${esc(it.server)}" data-key="${esc(it.key)}">
    <div class="poster">
      <div class="ph">${esc(it.title)}</div>
      <img loading="lazy" alt="" src="${poster(it)}" onload="this.classList.add('loaded')" onerror="this.remove()">
      <div class="tags"><span class="badge">${resLabel(it.res)}</span>${mineBadge(it)}</div>
      <div class="shade"><span>${bytes(it.size)}</span>${it.versions > 1 ? `<span>${t('lib.versions', { n: it.versions })}</span>` : ''}</div>
    </div>
    <div class="meta"><div class="title">${esc(it.title)}</div><div class="sub">${esc(sub)}</div></div>
  </div>`;
}
function moreTiles() {
  const next = gridItems.slice(gridShown, gridShown + 120);
  gridShown += next.length;
  $('#grid').insertAdjacentHTML('beforeend', next.map(tile).join(''));
  if (gridShown < gridItems.length) {
    const sentinel = document.createElement('div');
    sentinel.style.gridColumn = '1/-1';
    $('#grid').append(sentinel);
    new IntersectionObserver((ents, obs) => {
      if (ents.some((e) => e.isIntersecting)) { obs.disconnect(); sentinel.remove(); moreTiles(); }
    }, { rootMargin: '800px' }).observe(sentinel);
  }
}

// ==================================================================================================
// Detail panel
// ==================================================================================================
async function openDetail(kind, server, key) {
  const drawer = $('#drawer');
  const same = S.detail && S.detail.key === key && S.detail.server === server;
  if (!same) {
    drawer.innerHTML = `<div class="empty" style="padding-top:120px"><div class="spinner"></div></div>`;
    S.detail = { kind, server, key, data: null };
  }
  drawer.hidden = false; $('#drawer-backdrop').hidden = false; document.body.style.overflow = 'hidden';
  try {
    const d = await api(`/api/item/${kind}/${server}/${key}`);
    if (!S.detail || S.detail.key !== key) return;
    S.detail.data = d;
    renderDetail();
  } catch (e) {
    drawer.innerHTML = `<button class="icon-btn close-btn" id="close-btn" aria-label="${t('common.close')}">${I.x}</button><div class="empty">${esc(e.message)}</div>`;
    $('#close-btn').onclick = () => closeDrawer();
  }
}
function closeDrawer(nav = true) {
  if ($('#drawer').hidden) return;
  $('#drawer').hidden = true; $('#drawer-backdrop').hidden = true; document.body.style.overflow = '';
  S.detail = null;
  if (nav && location.hash.startsWith('#/item')) location.hash = '#/';
}
$('#drawer-backdrop').addEventListener('click', () => closeDrawer());
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeDrawer(); });

const why = (w) => t('why.' + w);
function eligibleLine(el) {
  return `<div class="res ${el.ok ? 'ok' : 'warn'}">${el.ok ? I.check : I.clock} ${el.ok ? t('el.offered', { why: why(el.why) }) : t('el.held', { why: why(el.why) })}</div>`;
}
function jobBadge(job) {
  if (!job) return '';
  const cls = { Completed: 'good', Failed: 'bad', Downloading: 'accent', Queued: '', Paused: 'warn', Deleted: '' }[job.status] ?? '';
  return `<span class="badge ${cls}">${t('job.' + job.status)}${job.status === 'Downloading' ? ` ${job.pct}%` : ''}</span>`;
}

function renderDetail() {
  const d = S.detail.data;
  const m = d.meta || {};
  const head = `
    <button class="icon-btn close-btn" id="close-btn" aria-label="${t('common.close')}">${I.x}</button>
    <div class="hero" style="background-image:url('${poster(d, 600)}')">
      <img class="poster-sm" src="${poster(d, 600)}" alt="">
      <div>
        <h2>${esc(d.title)}</h2>
        <div class="info">${[d.year, ...(m.genres || []), m.duration ? dur(m.duration / 1000) : ''].filter(Boolean).map(esc).join(' · ')}</div>
        <div class="chips"><span class="badge">${I.server} ${esc(serverName(d.server))}</span></div>
      </div>
    </div>`;
  const body = d.kind === 'movie' ? movieBody(d) : showBody(d);
  $('#drawer').innerHTML = head + `<div class="drawer-body">${m.summary ? `<p class="overview" id="overview">${esc(m.summary)}</p>` : ''}${body}</div>`;
  $('#close-btn').onclick = () => closeDrawer();
  $('#overview')?.addEventListener('click', (e) => e.target.classList.toggle('open'));
  $$('#drawer [data-fetch]').forEach((b) => b.addEventListener('click', () => fetchItem(b)));
}

function movieBody(d) {
  const mine = d.mine;
  const mineHtml = !mine
    ? `<p class="help muted">${t('detail.notInRadarr')}</p>`
    : mine.hasFile
      ? `<dl class="kv"><dt>${t('detail.quality')}</dt><dd>${esc(mine.quality || '–')}</dd><dt>${t('detail.size')}</dt><dd>${bytes(mine.size)}</dd><dt>${t('detail.file')}</dt><dd class="mono">${esc(mine.file)}</dd></dl>`
      : `<p class="help"><span class="badge warn">${t('mine.missing')}</span> ${mine.monitored ? t('detail.wanted') : t('detail.unmonitored')}</p>`;
  const versions = d.versions.map((v) => `
    <div class="file">
      <span class="badge">${resLabel(v.res)}</span>
      <div style="min-width:0">
        <div class="name" title="${esc(v.release)}">${esc(v.release)}</div>
        <div class="det">${[v.vcodec, v.acodec && (v.acodec + (v.channels ? ' ' + v.channels + 'ch' : '')), v.bitrate && num(v.bitrate / 1000, 1) + ' Mbps', v.container].filter(Boolean).map((x) => esc(String(x).toUpperCase())).join(' · ')}
          ${v.synthetic ? `<span class="badge" title="${esc(v.file)}">${t('detail.synthetic')}</span>` : ''} ${jobBadge(v.job)}</div>
      </div>
      <div class="sz num">${bytes(v.size)}</div>
    </div>`).join('');
  const canFetch = mine && (!mine.hasFile || d.versions.some((v) => RES_RANK[v.res] > (RES_RANK[(mine.quality || '').match(/\d+p/)?.[0]] || 0)));
  return `
    <div class="section-title">${t('detail.yours')}</div>
    <div class="card" style="padding:4px 0">${mineHtml.startsWith('<dl') ? mineHtml : `<div style="padding:12px 16px">${mineHtml}</div>`}</div>
    <div class="section-title">${t('detail.at', { name: esc(serverName(d.server)) })}</div>
    <div class="card"><div class="files" style="border-top:0">${versions}</div></div>
    <div class="estimate card" style="margin-top:14px">
      ${mine ? eligibleLine(d.eligible) : ''}
      <div class="actions">
        ${mine ? `<button class="btn primary" data-fetch="movie" ${canFetch ? '' : 'disabled'}>${I.download} ${t('detail.fetchRadarr')}</button>` : ''}
      </div>
      <div class="note">${I.eye} ${mine ? t('detail.fetchHelp') : t('detail.addFirst')}</div>
    </div>`;
}

function showBody(d) {
  const mine = d.mine;
  const seasons = d.seasons.map((s) => {
    const lack = s.episodes.filter((e) => e.mine && !e.mine.hasFile).length;
    const size = s.episodes.reduce((a, e) => a + e.size, 0);
    const rows = s.episodes.map((e) => `
      <div class="file">
        <span class="badge">E${String(e.episode).padStart(2, '0')}</span>
        <div style="min-width:0">
          <div class="name">${esc(e.title || '')}</div>
          <div class="det">${resLabel(e.res)} · ${bytes(e.size)}
            ${e.mine ? (e.mine.hasFile ? `<span class="badge good">${t('mine.have')}</span>` : `<span class="badge warn">${e.mine.monitored ? t('mine.missing') : t('mine.unmonitored')}</span>`) : ''}
            ${jobBadge(e.job)} ${e.eligible.ok && e.mine && !e.mine.hasFile ? `<span class="badge accent" title="${esc(why(e.eligible.why))}">${t('el.short')}</span>` : ''}</div>
        </div>
        <div class="sz">${e.mine && !e.mine.hasFile ? `<button class="btn sm" data-fetch="episode" data-season="${s.season}" data-episode="${e.episode}">${I.download}</button>` : ''}</div>
      </div>`).join('');
    return `<details class="card season" ${d.seasons.length === 1 ? 'open' : ''}>
      <summary>${I.chev.replace("<svg", "<svg class=\"chev\"")}<span>${s.season === 0 ? t('detail.specials') : t('detail.season', { n: s.season })}</span>
        <span class="right">${t('lib.episodes', { n: s.episodes.length })} · ${bytes(size)}${lack ? ` · <span class="good">${t('detail.lack', { n: lack })}</span>` : ''}</span></summary>
      ${lack && mine ? `<div style="padding:0 14px 12px"><button class="btn sm primary" data-fetch="season" data-season="${s.season}">${I.download} ${t('detail.fetchSeason', { n: lack })}</button></div>` : ''}
      <div class="files">${rows}</div>
    </details>`;
  }).join('');
  return `
    <div class="section-title">${t('detail.yours')}</div>
    <div class="card" style="padding:12px 16px">${mine ? `${esc(mine.title)} ${mine.monitored ? '' : `<span class="badge">${t('mine.unmonitored')}</span>`}` : `<span class="muted">${t('detail.notInSonarr')}</span>`}</div>
    <div class="section-title">${t('detail.at', { name: esc(serverName(d.server)) })}</div>
    ${seasons}
    <p class="note muted" style="font-size:12.5px">${t('detail.fetchHelpTv')}</p>`;
}

async function fetchItem(btn) {
  const d = S.detail.data;
  const body = { kind: d.kind, server: d.server, key: d.key };
  if (btn.dataset.season) body.season = +btn.dataset.season;
  if (btn.dataset.episode) body.episode = +btn.dataset.episode;
  btn.disabled = true;
  try {
    const r = await post('/api/fetch', body);
    toast(d.kind === 'movie' ? t('toast.searchRadarr') : t('toast.searchSonarr', { n: r.count }), 'good');
    setTimeout(() => { if (S.detail?.key === d.key) openDetail(d.kind, d.server, d.key); }, 6000);
  } catch (e) { toast(t('err.' + e.code) !== 'err.' + e.code ? t('err.' + e.code) : e.message, 'bad'); btn.disabled = false; }
}

// ==================================================================================================
// Downloads
// ==================================================================================================
// The page is updated every few seconds while it is open. Only the parts that changed are replaced, and a
// running download only gets its numbers updated, so posters aren't loaded again (that made the page flicker).
async function renderDownloads(data) {
  const v = $('#view');
  let root = $('#dl', v);
  if (!root && !S.dl) v.innerHTML = `<div class="empty"><div class="spinner"></div></div>`;
  try { S.dl = data || await api('/api/downloads'); } catch (e) { if (!root) v.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  const d = S.dl;
  const running = d.active.filter((j) => j.status === 'Downloading');
  const waiting = d.active.filter((j) => j.status !== 'Downloading');
  const speed = running.reduce((a, j) => a + j.speed, 0);
  const filter = store.get('dlFilter', 'all');
  const groups = historyGroups(d, filter);
  const counts = { all: d.done.length + (d.subs || []).length, video: d.done.length, sub: (d.subs || []).length };
  if (!root) {
    v.innerHTML = '<div id="dl"><div data-part="head"></div><div data-part="stats"></div><div data-part="current"></div><div data-part="queue"></div><div data-part="history"></div></div>';
    root = $('#dl', v);
    root.addEventListener('click', downloadsClick);
  }
  const part = (name) => $(`[data-part="${name}"]`, root);
  setHtml(part('head'), `
    <div class="toolbar">
      <div><h1 class="page">${t('dl.title')}</h1><p class="page-sub" style="margin:0">${t('dl.sub')}</p></div>
      <span class="spacer"></span>
      <button class="btn" data-pause="${d.paused ? 0 : 1}">${d.paused ? I.play : I.pause} ${d.paused ? t('dl.resumeAll') : t('dl.pauseAll')}</button>
    </div>
    ${d.paused ? `<div class="banner warn">${t('dl.paused')}</div>` : ''}`);
  setHtml(part('stats'), `
    <div class="stats">
      <div class="card stat feature"><div class="label">${t('dl.speed')}</div><div class="value num">${speed ? bytes(speed) + '/s' : '–'}</div><div class="sub">${t('dl.active', { n: running.length })}</div></div>
      <div class="card stat"><div class="label">${t('dl.waiting')}</div><div class="value num">${num(waiting.length)}</div><div class="sub">${bytes(waiting.reduce((a, j) => a + j.size - j.done, 0))}</div></div>
      <div class="card stat"><div class="label">${t('dl.total')}</div><div class="value num">${bytes(d.totals.bytes)}</div><div class="sub">${t('dl.files', { n: d.totals.count })}</div></div>
    </div>`);
  const cur = part('current');
  const curKey = running.map((j) => `${j.id}:${j.attempts}`).join(',');
  if (cur._key === curKey) running.forEach((j) => liveProgress(cur, j));
  else { cur.innerHTML = running.map(currentCard).join(''); cur._key = curKey; }
  setHtml(part('queue'), waiting.length ? `<div class="section-title">${t('dl.queue')}</div><div class="card qlist">${waiting.map(queueRow).join('')}</div>` : '');
  setHtml(part('history'), `
    <div class="section-title hist-title"><span>${t('dl.history')}</span>
      <div class="seg hfilter" role="group" aria-label="${t('dl.filter')}">${[['all', t('dl.fAll')], ['video', t('dl.fVideo')], ['sub', t('dl.fSubs')]]
        .map(([k, l]) => `<button type="button" data-filter="${k}" class="${filter === k ? 'on' : ''}" aria-pressed="${filter === k}">${l} <span class="n num">${counts[k]}</span></button>`).join('')}</div></div>
    ${groups.length ? `<div class="card">${groups.map(historyGroup).join('')}</div>`
      : `<div class="empty">${I.inbox}<p>${counts.all ? t('dl.noneFilter') : t('dl.none')}</p></div>`}`);
}
const setHtml = (el, html) => { if (el._html !== html) { el.innerHTML = html; el._html = html; } };
async function downloadsClick(e) {
  const f = e.target.closest('[data-filter]');
  if (f) { store.set('dlFilter', f.dataset.filter); renderDownloads(S.dl); return; }
  const p = e.target.closest('[data-pause]');
  if (p) { await post('/api/pause', { paused: p.dataset.pause === '1' }); renderDownloads(); return; }
  const b = e.target.closest('[data-act]');
  if (!b) return;
  if (b.dataset.act === 'delete' && !confirm(t('dl.confirmDelete'))) return;
  await post(`/api/downloads/${b.dataset.id}/${b.dataset.act}`);
  renderDownloads();
}
// Updates the numbers of a running download in place.
function liveProgress(el, j) {
  const card = el.querySelector(`[data-job="${j.id}"]`);
  if (!card) return;
  const pct = j.size ? (j.done / j.size) * 100 : 0;
  const set = (k, text) => { const x = card.querySelector(`[data-live="${k}"]`); if (x && x.textContent !== text) x.textContent = text; };
  card.querySelector('[data-live="bar"]').style.width = pct.toFixed(1) + '%';
  set('pct', num(pct, 1) + '%');
  set('done', `${bytes(j.done)} / ${bytes(j.size)}`);
  set('speed', j.speed ? bytes(j.speed) + '/s' : '–');
  set('eta', j.eta != null ? dur(j.eta) : '–');
}
// Where it comes from: the friend's Plex server and the friend who shares it.
const source = (x) => {
  const src = x.source;
  if (!src?.name) return '';
  const who = src.owner && src.owner !== src.name ? ` · ${src.owner}` : '';
  return `<span class="src" title="${esc(t('dl.from'))}">${I.server} ${esc(src.name + who)}</span>`;
};
const jobPoster = (j) => (j.key ? `/api/poster/${j.server}/${j.key}?w=300` : '');
function currentCard(j) {
  const pct = j.size ? (j.done / j.size) * 100 : 0;
  return `<div class="card current" data-job="${j.id}">
    <img class="poster-sm" src="${jobPoster(j)}" alt="" onerror="this.style.visibility='hidden'">
    <div style="min-width:0">
      <h3>${esc(j.label)}</h3>
      <div class="muted" style="font-size:13.5px">${esc(j.sublabel || '')}</div>
      ${j.source ? `<div class="muted" style="font-size:13px;margin-top:4px">${source(j)}</div>` : ''}
      <div class="stage">${I.download} ${t('job.Downloading')}${j.attempts ? ` · ${t('dl.retry', { n: j.attempts })}` : ''}</div>
      <div class="pbar"><i data-live="bar" style="width:${pct.toFixed(1)}%"></i></div>
      <div class="metrics">
        <div class="metric"><div class="label">${t('dl.progress')}</div><div class="value num" data-live="pct">${num(pct, 1)}%</div></div>
        <div class="metric"><div class="label">${t('dl.done')}</div><div class="value num" data-live="done">${bytes(j.done)} / ${bytes(j.size)}</div></div>
        <div class="metric"><div class="label">${t('dl.speed')}</div><div class="value num" data-live="speed">${j.speed ? bytes(j.speed) + '/s' : '–'}</div></div>
        <div class="metric"><div class="label">${t('dl.eta')}</div><div class="value num" data-live="eta">${j.eta != null ? dur(j.eta) : '–'}</div></div>
      </div>
      <div class="mono muted" style="margin-top:10px">${esc(j.name)}</div>
      <div class="actions">
        <button class="btn sm" data-act="pause" data-id="${j.id}">${I.pause} ${t('dl.pause')}</button>
        <button class="btn sm danger" data-act="delete" data-id="${j.id}">${I.trash} ${t('dl.cancel')}</button>
      </div>
    </div>
  </div>`;
}
function queueRow(j, i) {
  return `<div class="qrow">
    <div class="pos">${i + 1}</div>
    <img src="${jobPoster(j)}" alt="" onerror="this.style.visibility='hidden'">
    <div style="min-width:0"><div class="t">${esc(j.label)} <span class="muted">${esc(j.sublabel || '')}</span></div><div class="s">${esc(j.name)}</div>
      ${j.source ? `<div class="s">${source(j)}</div>` : ''}
      ${j.message ? `<div class="s" style="color:var(--warn)">${esc(j.message)}</div>` : ''}</div>
    <div class="r">${bytes(j.size)}<br><span class="badge ${j.status === 'Paused' ? 'warn' : ''}">${t('job.' + j.status)}</span></div>
    <div class="btns">
      <button class="icon-btn" data-act="${j.status === 'Paused' ? 'resume' : 'pause'}" data-id="${j.id}" title="${j.status === 'Paused' ? t('dl.resume') : t('dl.pause')}">${j.status === 'Paused' ? I.play : I.pause}</button>
      <button class="icon-btn" data-act="delete" data-id="${j.id}" title="${t('dl.cancel')}">${I.trash}</button>
    </div>
  </div>`;
}
// History: one block per movie or episode, with the video and the subtitles that came in for it. The newest first.
function historyGroups(d, filter) {
  const items = [...d.done.map((x) => ({ type: 'video', at: x.finished || x.created, x })), ...(d.subs || []).map((x) => ({ type: 'sub', at: x.at, x }))]
    .filter((it) => filter === 'all' || it.type === filter)
    .sort((a, b) => b.at - a.at);
  const groups = new Map();
  for (const it of items) {
    const k = `${it.x.kind}|${it.x.label}|${it.x.sublabel || ''}`;
    if (!groups.has(k)) groups.set(k, { head: it.x, items: [] });
    groups.get(k).items.push(it);
  }
  return [...groups.values()];
}
// Release names only break after a dot, dash, underscore or bracket, not in the middle of a word.
const breakable = (s) => esc(s).replace(/([._\-)\]])/g, '$1<wbr>');
const srcKey = (x) => (x.source ? `${x.source.name}|${x.source.owner}` : '');
function historyGroup(g) {
  const pic = g.items.find((it) => it.x.key)?.x;
  const oneSource = new Set(g.items.map((it) => srcKey(it.x))).size === 1;
  return `<div class="hgroup">
    ${pic ? `<img class="hposter" src="${jobPoster(pic)}" alt="" loading="lazy" onerror="this.style.visibility='hidden'">` : '<div class="hposter"></div>'}
    <div class="hhead"><div class="t">${esc(g.head.label)}${g.head.sublabel ? ` <span class="muted">${esc(g.head.sublabel)}</span>` : ''}</div>
      ${oneSource && g.head.source ? `<div class="s">${source(g.head)}</div>` : ''}</div>
    <div class="hitems">${g.items.map((it) => (it.type === 'sub' ? subItem(it.x, !oneSource) : videoItem(it.x, !oneSource))).join('')}</div>
  </div>`;
}
const meta = (size, when_) => `<div class="hmeta num"><span>${size}</span><span class="muted">${when_}</span></div>`;
function videoItem(j, showSource) {
  const cls = { Completed: 'good', Failed: 'bad', Deleted: '' }[j.status] ?? '';
  const took = j.finished && j.started ? j.finished - j.started : null;
  return `<div class="hitem video">
    <span class="hic" title="${t('dl.video')}">${j.kind === 'movie' ? I.film : I.tv}</span>
    <div class="hmain">
      <div class="hline"><b>${t('dl.video')}</b> <span class="badge ${cls}">${t('job.' + j.status)}</span></div>
      <div class="s mono rel">${breakable(j.name)}</div>
      ${showSource && j.source ? `<div class="s">${source(j)}</div>` : ''}
      ${j.status === 'Failed' ? `<div class="s bad-text">${esc(j.message)}</div>` : ''}
      ${j.status === 'Completed' ? `<div class="s">${j.removed ? t('dl.imported') : t('dl.waitImport')}</div>` : ''}
      ${j.subs?.length ? `<div class="subchips" title="${t('dl.subsAlong')}">${j.subs.map((x) => `<span class="subchip">${I.subtitles} ${esc(langName(x.lang))}${x.forced ? ` (${t('dl.forced')})` : ''}</span>`).join('')}</div>` : ''}
      ${j.status === 'Failed' ? `<button class="btn sm" data-act="retry" data-id="${j.id}" style="margin-top:6px">${I.retry} ${t('dl.retryBtn')}</button>` : ''}
    </div>
    ${meta(bytes(j.size) + (took ? ` · ${dur(took)}` : ''), when(j.finished || j.created))}
  </div>`;
}
function subItem(x, showSource) {
  return `<div class="hitem sub">
    <span class="hic" title="${t('dl.subBadge')}">${I.subtitles}</span>
    <div class="hmain">
      <div class="hline htext"><b>${t('dl.subBadge')}</b> · ${esc(langName(x.lang))}${x.hi ? ` (${t('dl.hi')})` : ''} <span class="muted">· ${t('dl.viaBazarr')}</span></div>
      ${showSource && x.source ? `<div class="s">${source(x)}</div>` : ''}
    </div>
    ${meta(bytes(x.size || 0) + (x.ext ? ` · ${esc(x.ext)}` : ''), when(x.at))}
  </div>`;
}

// ==================================================================================================
// Activity
// ==================================================================================================
async function renderActivity() {
  const v = $('#view');
  v.innerHTML = `<div class="empty"><div class="spinner"></div></div>`;
  const ev = await api('/api/events?limit=300');
  const cls = { done: 'good', failed: 'bad', grab: 'accent', search: '', sweep: 'warn', request: 'accent', setup: '', subtitles: '' };
  v.innerHTML = `
    <h1 class="page">${t('act.title')}</h1><p class="page-sub">${t('act.sub')}</p>
    ${ev.length ? `<div class="card">${ev.map((e) => { const [line, sub] = eventText(e); return `<div class="hrow">
      <div style="min-width:0"><div class="t" style="font-weight:550">${esc(line)}</div>${sub ? `<div class="s">${esc(sub)}</div>` : ''}</div>
      <div class="r"><span class="badge ${cls[e.kind] ?? ''}">${t('ev.' + e.kind)}</span><br><span class="muted">${when(e.at)}</span></div>
    </div>`; }).join('')}</div>` : `<div class="empty">${I.activity}<p>${t('act.none')}</p></div>`}`;
}

// A language tag ("nl", "pt-BR") or one of Bazarr's names ("dutch") in the UI language, where the browser knows it.
const LANG_CODES = { dutch: 'nl', english: 'en', german: 'de', french: 'fr', spanish: 'es', italian: 'it', portuguese: 'pt',
  brazilian_portuguese: 'pt-BR', danish: 'da', swedish: 'sv', norwegian: 'no', finnish: 'fi', polish: 'pl', turkish: 'tr' };
function langName(l) {
  const code = LANG_CODES[l] || (/^[a-z]{2,3}(-[A-Za-z0-9]+)*$/.test(l || '') ? l : null);
  try { if (code) return new Intl.DisplayNames([lang()], { type: 'language' }).of(code); } catch { /* old browser, odd tag */ }
  return (l || '').replace(/[_-]/g, ' ');
}

// [line, explanation] in the UI language. Lines logged before events carried data only have their English text.
function eventText(e) {
  const d = e.data;
  if (!d) return [e.text, ''];
  switch (e.kind) {
    case 'search': {
      const held = Object.entries(d.held || {});
      const n = held.reduce((a, [, c]) => a + c, 0);
      const reasons = held.map(([w, c]) => (held.length > 1 ? `${c}× ` : '') + why(w)).join(', ');
      const result = [d.offered ? t('evt.offered', { n: d.offered }) : t('evt.none'), n ? t('evt.held', { n, why: reasons }) : '']
        .filter(Boolean).join(', ');
      return [t(d.app ? 'evt.search' : 'evt.searchAny', { app: d.app, what: d.what, result }),
        d.offered && d.app ? t('evt.searchHelp', { app: d.app }) : ''];
    }
    case 'sweep': return [t('evt.sweep', { app: d.app, items: d.items.join(', ') }), t('evt.sweepHelp')];
    case 'request': return [d.n == null ? t('evt.request', d) : t('evt.requestEps', d), ''];
    case 'grab': return [t('evt.grab', d), d.name];
    case 'failed': return [t('evt.failed', d), d.msg];
    case 'done': return [t('evt.done', { what: d.what, size: bytes(d.size) }), ''];
    case 'setup': return [t(d.active ? 'evt.setup' : 'evt.setupOff', d), ''];
    case 'subtitles': return [t(d.found ? 'evt.subs' : 'evt.subsNone', { what: d.what, n: d.found, lang: langName(d.lang) }), ''];
    default: return [e.text, ''];
  }
}

// ==================================================================================================
// Polling, login and start
// ==================================================================================================
async function poll() {
  try {
    S.status = await api('/api/status');
    const dl = await api('/api/downloads');
    const running = dl.active.filter((j) => j.status === 'Downloading');
    $('#dl-count').hidden = !dl.active.length;
    $('#dl-count').textContent = dl.active.length;
    const mp = $('#mini-progress');
    if (running.length) {
      const done = running.reduce((a, j) => a + j.done, 0), size = running.reduce((a, j) => a + j.size, 0);
      const speed = running.reduce((a, j) => a + j.speed, 0);
      mp.hidden = false;
      mp.innerHTML = `${I.download}<div class="bar"><i style="width:${((done / size) * 100).toFixed(1)}%"></i></div><span class="txt num">${speed ? bytes(speed) + '/s' : ''}</span>`;
    } else mp.hidden = true;
    if (S.route === 'downloads' && !document.hidden) renderDownloads(dl);
  } catch { /* offline: try again later */ }
}
$('#mini-progress').addEventListener('click', () => { location.hash = '#/downloads'; });

function renderLogin() {
  document.body.classList.add('login-mode');
  $('#view').innerHTML = `
    <form class="card login" id="login-form">
      <img src="/static/icon.svg" alt="" width="56" height="56">
      <h1>PlexArr</h1>
      <label>${t('login.user')}<input class="input" name="user" autocomplete="username" required></label>
      <label>${t('login.password')}<input class="input" name="password" type="password" autocomplete="current-password" required></label>
      <p class="login-err" id="login-err" hidden>${t('login.wrong')}</p>
      <button class="btn primary lg" type="submit">${I.lock} ${t('login.submit')}</button>
    </form>`;
  $('#login-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target), btn = $('button', e.target);
    btn.disabled = true;
    try { await post('/api/auth/login', { user: fd.get('user'), password: fd.get('password') }); location.reload(); } catch { $('#login-err').hidden = false; btn.disabled = false; }
  });
  $('input', $('#login-form')).focus();
}
events.addEventListener('login', () => { if (S.auth.loggedIn) { S.auth.loggedIn = false; renderLogin(); } });

applyStaticTexts();
(async () => {
  try { S.auth = await api('/api/auth/status'); } catch { /* network error: try anyway */ }
  if (!S.auth.loggedIn) { renderLogin(); return; }
  $('#logout-btn').hidden = !S.auth.enabled;
  try { S.setupDone = (await api('/api/setup')).setupDone; } catch { /* keep default */ }
  await poll();
  await route(true);
  setInterval(poll, 3000);
})();
