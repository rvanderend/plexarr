// Settings: Plex and the friends' servers, Sonarr/Radarr and the link with them, behaviour, downloads, login.
import { $, $$, esc, t, I, api, post, put, toast, when, lang, setLang } from './util.js';
import { applyStaticTexts } from './app.js';

export const field = (k, label, value, opts = {}) => `<label class="ff"><span class="ff-l">${label}</span>
  <input class="input" data-k="${k}" type="${opts.type || 'text'}" value="${esc(value ?? '')}" ${opts.ph ? `placeholder="${esc(opts.ph)}"` : ''} ${opts.min != null ? `min="${opts.min}"` : ''} autocomplete="off" spellcheck="false">
  ${opts.help ? `<span class="ff-h">${opts.help}</span>` : ''}</label>`;
export const sw = (k, label, on, help = '') => `<div class="srow"><label class="switch"><input type="checkbox" data-k="${k}" ${on ? 'checked' : ''}><span></span></label>
  <div><b style="font-weight:600">${label}</b>${help ? `<div class="ff-h">${help}</div>` : ''}</div></div>`;
export const res = (ok, text) => `<span class="res ${ok ? 'ok' : 'bad'}">${ok ? I.check : I.x} ${esc(text)}</span>`;

/** Signs in with Plex: opens app.plex.tv with a PIN and waits until it is approved. Returns a function that stops waiting. */
export function plexSignIn(done) {
  const w = window.open('', '_blank');  // open it right away: a window opened after an await gets blocked
  let stopped = false;
  const stop = () => { stopped = true; };
  (async () => {
    try {
      const pin = await post('/api/plex/pin');
      if (!w) { done({ ok: false, error: t('plex.popup') }); return; }
      w.location = pin.url;
      for (let i = 0; i < 300 && !stopped; i++) {
        await new Promise((r) => setTimeout(r, 2000));
        const closed = w.closed;
        const r = await api('/api/plex/pin/' + pin.id);
        if (r.ok) { try { w.close(); } catch { /* already closed */ } done({ ok: true }); return; }
        if (closed) { done({ ok: false, error: t('plex.cancelled') }); return; }
      }
      if (!stopped) done({ ok: false, error: t('plex.timeout') });
    } catch (e) { w?.close(); done({ ok: false, error: e.message }); }
  })();
  return stop;
}

// The base URL for Bazarr's Subsarr provider: Bazarr sends no API key, so it goes in the path.
const bazarrUrl = (s, key) => `${s.self_url || s.selfUrlDefault}/subsarr/${key}`;

export async function renderSettings(S) {
  const v = $('#view');
  v.innerHTML = `<div class="empty"><div class="spinner"></div></div>`;
  const [s, st, key] = await Promise.all([api('/api/settings'), api('/api/status'), api('/api/apikey')]);
  S.status = st;
  const sections = [['plex', 'Plex'], ['arr', 'Sonarr & Radarr'], ['link', t('set.link')], ['behaviour', t('set.behaviour')],
    ['download', t('set.download')], ['login', t('set.login')]];
  v.innerHTML = `
    <h1 class="page">${t('nav.settings')}</h1><p class="page-sub">${t('set.sub')}</p>
    <nav class="set-nav">${sections.map(([id, l]) => `<a href="#/settings" data-to="${id}">${l}</a>`).join('')}</nav>
    <div class="settings">
      <section class="card" id="sec-plex">
        <h3>Plex</h3><p class="help">${t('set.plexHelp')}</p>
        <div class="srow"><button class="btn" id="plex-signin">${I.lock} ${t('wiz.plex.signIn')}</button><span id="plex-signin-res"></span></div>
        <div class="form">${field('plex_token', t('set.plexToken'), s.plex_token, { help: t('set.plexTokenHelp') })}</div>
        <div class="actions"><button class="btn" id="find-servers">${I.search} ${t('set.findServers')}</button></div>
        <div class="checks" id="server-list">${st.servers.map((x) => serverRow({ ...x, ok: !x.error }, true)).join('')}</div>
      </section>

      <section class="card" id="sec-arr">
        <h3>Sonarr &amp; Radarr</h3><p class="help">${t('set.arrHelp')}</p>
        ${['radarr', 'sonarr'].map((a) => `<div class="conn">
          <div class="conn-h"><b>${a === 'radarr' ? 'Radarr' : 'Sonarr'}</b><span class="muted">${a === 'radarr' ? t('set.movies') : t('set.series')}</span><span id="test-${a}"></span></div>
          <div class="form">${field(a + '_url', 'URL', s[a + '_url'], { ph: a === 'radarr' ? 'http://radarr:7878' : 'http://sonarr:8989' })}${field(a + '_api_key', t('set.apiKey'), s[a + '_api_key'])}</div>
          <div class="actions"><button class="btn sm" data-test="${a}">${t('set.test')}</button></div></div>`).join('')}
      </section>

      <section class="card" id="sec-link">
        <h3>${t('set.link')}</h3><p class="help">${t('set.linkHelp')}</p>
        ${['radarr', 'sonarr'].map((a) => linkRow(a, st)).join('')}
        <h4>${t('set.manual')}</h4><p class="help">${t('set.manualHelp')}</p>
        <dl class="kv card" style="box-shadow:none">
          <dt>Newznab URL</dt><dd class="mono">${esc((s.self_url || s.selfUrlDefault))}</dd>
          <dt>API path</dt><dd class="mono">/newznab/api</dd>
          <dt>SABnzbd</dt><dd class="mono">${esc((s.self_url || s.selfUrlDefault).replace(/^https?:\/\//, ''))} · URL base <b>sabnzbd</b></dd>
          <dt>API key</dt><dd class="mono"><span id="apikey">${esc(key.key)}</span>
            <button class="btn sm ghost" id="copy-key">${I.copy}</button><button class="btn sm ghost" id="new-key">${I.refresh}</button></dd>
        </dl>
        <div class="form">${field('self_url', t('set.selfUrl'), s.self_url, { ph: s.selfUrlDefault, help: t('set.selfUrlHelp') })}</div>
        <h4>Bazarr</h4><p class="help">${t('set.bazarrHelp')}</p>
        <dl class="kv card" style="box-shadow:none">
          <dt>Provider</dt><dd>Subsarr</dd>
          <dt>Base URL</dt><dd class="mono"><span id="bazarr-url">${esc(bazarrUrl(s, key.key))}</span>
            <button class="btn sm ghost" id="copy-bazarr">${I.copy}</button></dd>
        </dl>
      </section>

      <section class="card" id="sec-behaviour">
        <h3>${t('set.behaviour')}</h3><p class="help">${t('set.behaviourHelp')}</p>
        <div class="presets" id="mode">
          ${['fallback', 'equal'].map((m) => `<button class="preset ${s.mode === m ? 'on' : ''}" data-mode="${m}"><b>${t('mode.' + m)}</b><span>${t('mode.' + m + '.help')}</span></button>`).join('')}
        </div>
        <div id="fallback-opts" ${s.mode === 'fallback' ? '' : 'hidden'}>
          <div class="form">${field('wait_hours', t('set.waitHours'), s.wait_hours, { type: 'number', min: 0, help: t('set.waitHoursHelp') })}</div>
          <h4>${t('set.sweep')}</h4>
          ${sw('sweep_enabled', t('set.sweepOn'), s.sweep_enabled, t('set.sweepHelp'))}
          <div class="form">
            ${field('sweep_per_run', t('set.sweepPerRun'), s.sweep_per_run, { type: 'number', min: 0 })}
            ${field('sweep_repeat_days', t('set.sweepRepeat'), s.sweep_repeat_days, { type: 'number', min: 1 })}
          </div>
          <div class="actions"><button class="btn sm" id="sweep-now">${I.play} ${t('set.sweepNow')}</button>
            <span class="muted" style="font-size:13px">${st.sweepAt ? t('set.sweepLast', { when: when(st.sweepAt) }) : ''}</span></div>
        </div>
      </section>

      <section class="card" id="sec-download">
        <h3>${t('set.download')}</h3><p class="help">${t('set.downloadHelp', { dir: esc(s.downloadDir) })}</p>
        <div class="form">
          ${field('max_downloads', t('set.maxDownloads'), s.max_downloads, { type: 'number', min: 1, help: t('set.maxDownloadsHelp') })}
          ${field('speed_limit', t('set.speedLimit'), s.speed_limit, { type: 'number', min: 0, help: t('set.speedLimitHelp') })}
          ${field('refresh_minutes', t('set.refresh'), s.refresh_minutes, { type: 'number', min: 5, help: t('set.refreshHelp') })}
        </div>
        ${sw('subtitles', t('set.subtitles'), s.subtitles, t('set.subtitlesHelp'))}
        <h4>${t('set.language')}</h4>
        <div class="seg" id="lang-seg">${[['nl', 'Nederlands'], ['en', 'English']].map(([k, l]) => `<button data-l="${k}" class="${lang() === k ? 'on' : ''}">${l}</button>`).join('')}</div>
        <div class="actions"><a class="btn sm" href="#/setup">${t('set.rerunWizard')}</a></div>
      </section>

      <section class="card" id="sec-login">
        <h3>${t('set.login')}</h3><p class="help">${t('set.loginHelp')}</p>
        ${sw('auth_on', t('set.loginOn'), s.auth_enabled)}
        <div class="form">
          <label class="ff"><span class="ff-l">${t('login.user')}</span><input class="input" id="auth-user" value="${esc(s.auth_user)}" autocomplete="username"></label>
          <label class="ff"><span class="ff-l">${t('login.password')}</span><input class="input" id="auth-pass" type="password" autocomplete="new-password" placeholder="${s.auth_enabled ? t('set.unchanged') : ''}"></label>
        </div>
        <div class="actions"><button class="btn" id="auth-save">${t('common.save')}</button></div>
      </section>

      <div class="actions" style="position:sticky;bottom:16px"><button class="btn primary lg" id="save">${t('common.save')}</button></div>
    </div>`;

  $$('.set-nav a').forEach((a) => a.addEventListener('click', (e) => { e.preventDefault(); $('#sec-' + a.dataset.to).scrollIntoView({ behavior: 'smooth' }); }));
  let mode = s.mode;
  $('#mode').addEventListener('click', (e) => {
    const b = e.target.closest('[data-mode]'); if (!b) return;
    mode = b.dataset.mode;
    $$('#mode .preset').forEach((x) => x.classList.toggle('on', x === b));
    $('#fallback-opts').hidden = mode !== 'fallback';
  });
  const values = () => {
    const out = { mode };
    $$('.settings [data-k]').forEach((el) => {
      if (el.dataset.k === 'auth_on') return;
      out[el.dataset.k] = el.type === 'checkbox' ? el.checked : el.type === 'number' ? +el.value : el.value;
    });
    out.servers = $$('#server-list input[type=checkbox]:checked').map((x) => x.value);
    return out;
  };
  $('#save').onclick = async () => {
    try { await put('/api/settings', values()); toast(t('set.saved'), 'good'); S.lib = null; renderSettings(S); } catch (e) { toast(e.message, 'bad'); }
  };
  $('#plex-signin').onclick = () => {
    $('#plex-signin-res').innerHTML = `<span class="spinner"></span> ${t('wiz.plex.waiting')}`;
    plexSignIn(async (r) => {
      if (!r.ok) { $('#plex-signin-res').innerHTML = res(false, r.error); return; }
      $('#plex-signin-res').innerHTML = res(true, t('wiz.plex.signedIn'));
      $('[data-k=plex_token]').value = (await api('/api/settings')).plex_token;
      $('#find-servers').click();
    });
  };
  $('#find-servers').onclick = async () => {
    const b = $('#find-servers'); b.disabled = true;
    const chosen = new Set($$('#server-list input:checked').map((x) => x.value));
    try {
      const list = await post('/api/plex/servers', { token: $('[data-k=plex_token]').value });
      $('#server-list').innerHTML = list.length ? list.map((x) => serverRow(x, chosen.has(x.id) || st.servers.some((y) => y.id === x.id))).join('')
        : `<p class="help">${t('set.noServers')}</p>`;
    } catch (e) { toast(e.message, 'bad'); }
    b.disabled = false;
  };
  $$('[data-test]').forEach((b) => b.addEventListener('click', async () => {
    const a = b.dataset.test;
    $('#test-' + a).innerHTML = '<span class="spinner"></span>';
    const r = await post('/api/arr/test', { app: a, url: $(`[data-k=${a}_url]`).value, key: $(`[data-k=${a}_api_key]`).value });
    $('#test-' + a).innerHTML = r.ok ? res(true, `${r.app} ${r.version}`) : res(false, r.error === 'api_key' ? t('set.wrongKey') : r.error);
  }));
  $$('[data-setup]').forEach((b) => b.addEventListener('click', async () => {
    b.disabled = true;
    try {
      await put('/api/settings', values());
      await post('/api/arr/setup', { app: b.dataset.setup, active: b.dataset.active === '1' });
      toast(t('set.linked'), 'good');
    } catch (e) { toast(e.message, 'bad'); }
    renderSettings(S);
  }));
  const copy = (id) => () => { navigator.clipboard?.writeText($(id).textContent).then(() => toast(t('set.copied'))); };
  $('#copy-key').onclick = copy('#apikey');
  $('#copy-bazarr').onclick = copy('#bazarr-url');
  $('#new-key').onclick = async () => {
    if (!confirm(t('set.newKeyConfirm'))) return;
    const r = await post('/api/apikey');
    $('#apikey').textContent = r.key;
    $('#bazarr-url').textContent = bazarrUrl(s, r.key);
    toast(t('set.newKeyDone'));
  };
  $('#sweep-now').onclick = async () => {
    try {
      await put('/api/settings', values());
      const r = await post('/api/sweep');
      const n = r.movies.length + r.episodes.length;
      toast(n ? t('set.sweepDone', { list: [...r.movies, ...r.episodes].join(', ') }) : t('set.sweepNothing'), 'good');
    } catch (e) { toast(e.message, 'bad'); }
  };
  $('#lang-seg').addEventListener('click', (e) => {
    const b = e.target.closest('button'); if (!b) return;
    setLang(b.dataset.l); applyStaticTexts(); renderSettings(S);
  });
  $('#auth-save').onclick = async () => {
    try {
      await put('/api/auth', { enabled: $('[data-k=auth_on]').checked, user: $('#auth-user').value, password: $('#auth-pass').value });
      toast(t('set.saved'), 'good');
    } catch (e) { toast(t('err.' + e.code) !== 'err.' + e.code ? t('err.' + e.code) : e.message, 'bad'); }
  };
}

export function serverRow(x, checked) {
  const state = x.ok === false ? res(false, x.error || t('set.offline'))
    : x.downloads === false ? res(false, t('set.noDownloads'))
      : x.downloads ? res(true, t('set.downloadsOk', { via: x.via || '' })) : (x.files != null ? `<span class="muted" style="font-size:13px">${t('set.files', { n: x.files })}</span>` : '');
  return `<label class="check-row"><input type="checkbox" class="cb" value="${esc(x.id)}" ${checked ? 'checked' : ''}>
    <div><b>${esc(x.name)}</b> ${x.owner ? `<span class="muted">· ${esc(x.owner)}</span>` : ''}<div>${state}</div></div></label>`;
}

export function linkRow(a, st) {
  const name = a === 'radarr' ? 'Radarr' : 'Sonarr';
  if (!st[a]) return `<div class="conn"><div class="conn-h"><b>${name}</b><span class="muted">${t('set.notConfigured')}</span></div></div>`;
  const l = st.links[a] || {};
  const state = l.error ? res(false, l.error)
    : !l.indexer || !l.client ? `<span class="res warn">${I.clock} ${t('set.notLinked')}</span>`
      : !l.linked ? res(false, t('set.badLink'))
        : l.active ? res(true, t('set.active')) : `<span class="res warn">${I.pause} ${t('set.inactive')}</span>`;
  return `<div class="conn"><div class="conn-h"><b>${name}</b>${state}</div>
    <div class="actions" style="margin-top:4px">
      ${l.active ? `<button class="btn sm" data-setup="${a}" data-active="0">${I.pause} ${t('set.deactivate')}</button>`
        : `<button class="btn sm primary" data-setup="${a}" data-active="1">${I.play} ${l.indexer ? t('set.activate') : t('set.setupActive')}</button>
           ${l.indexer ? '' : `<button class="btn sm" data-setup="${a}" data-active="0">${t('set.setupInactive')}</button>`}`}
    </div></div>`;
}
