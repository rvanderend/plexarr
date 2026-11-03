// Setup wizard: language → Plex → Sonarr/Radarr → download folder → link → behaviour → login → done.
import { $, $$, esc, t, lang, setLang, I, icon, api, post, put } from './util.js';
import { applyStaticTexts } from './app.js';
import { field, sw, res, serverRow, linkRow, plexSignIn } from './settings.js';

const STEPS = ['welcome', 'plex', 'arr', 'folder', 'link', 'behaviour', 'login', 'done'];
let step = 0;
let S_ = null;
let s = null;          // the settings, as /api/settings returns them
let stopSignIn = null; // stops waiting for a Plex sign-in when the step changes

function frame(body, { back = true, next = t('wiz.next'), skip = false } = {}) {
  const name = STEPS[step];
  return `
    <div class="wizard">
      <div class="wiz-head">
        <img src="/static/icon.svg" alt="" width="36" height="36">
        <div><div class="wiz-brand">PlexArr</div><div class="muted">${t('wiz.stepOf', { n: step + 1, total: STEPS.length })}</div></div>
      </div>
      <ol class="wiz-steps" aria-label="${t('wiz.progress')}">${STEPS.map((x, i) => `<li class="${i < step ? 'done' : i === step ? 'on' : ''}" ${i === step ? 'aria-current="step"' : ''}><span>${t('wiz.s.' + x)}</span></li>`).join('')}</ol>
      <section class="card wiz-card">
        <h1>${t('wiz.' + name + '.title')}</h1>
        <p class="help">${t('wiz.' + name + '.help')}</p>
        ${body}
        <div class="wiz-err" id="wiz-err" hidden></div>
        <div class="wiz-foot">
          ${back && step > 0 ? `<button class="btn ghost" id="wiz-back">${I.back} ${t('wiz.back')}</button>` : '<span></span>'}
          <span class="spacer"></span>
          ${skip ? `<button class="btn ghost" id="wiz-skip">${t('wiz.skip')}</button>` : ''}
          ${next ? `<button class="btn primary" id="wiz-next">${next} ${I.chev}</button>` : ''}
        </div>
      </section>
    </div>`;
}
const err = (msg) => { const e = $('#wiz-err'); e.hidden = !msg; e.innerHTML = msg ? `${icon('x', 16)} ${esc(msg)}` : ''; };
const go = (n) => { step = Math.max(0, Math.min(STEPS.length - 1, n)); draw(); window.scrollTo(0, 0); };
const reload = async () => { s = await api('/api/settings'); };

async function draw() {
  stopSignIn?.(); stopSignIn = null;
  const v = $('#view');
  const name = STEPS[step];

  if (name === 'welcome') {
    v.innerHTML = frame(`
      <div class="lang-pick">${[['en', 'English'], ['nl', 'Nederlands']].map(([k, l]) => `
        <button class="preset ${lang() === k ? 'on' : ''}" data-lang="${k}">${icon('globe', 20)}<b>${l}</b></button>`).join('')}</div>
      <ul class="wiz-list">${['1', '2', '3'].map((k) => `<li>${t('wiz.welcome.point' + k)}</li>`).join('')}</ul>`, { back: false, next: t('wiz.start') });
    $$('[data-lang]').forEach((b) => { b.onclick = async () => {
      setLang(b.dataset.lang); await put('/api/settings', { language: b.dataset.lang }); applyStaticTexts(); draw();
    }; });
  }

  if (name === 'plex') {
    v.innerHTML = frame(`
      <div class="srow"><button class="btn primary" id="w-signin">${I.lock} ${t('wiz.plex.signIn')}</button><span id="w-signin-res">${s.plex_token ? res(true, t('wiz.plex.signedIn')) : ''}</span></div>
      <details class="wiz-more"><summary>${t('wiz.plex.byHand')}</summary>
        <div class="form">${field('plex_token', t('set.plexToken'), s.plex_token, { help: t('set.plexTokenHelp') })}</div>
        <div class="actions"><button class="btn sm" id="w-find">${I.search} ${t('set.findServers')}</button></div>
      </details>
      <div class="checks" id="w-servers"></div>`);
    const list = async (token = '') => {
      const box = $('#w-servers');
      box.innerHTML = `<div class="srow"><span class="spinner"></span> ${t('wiz.plex.looking')}</div>`;
      try {
        const found = await post('/api/plex/servers', { token });
        // Nothing chosen yet: tick every server that allows downloads.
        const pick = (x) => (s.servers.length ? s.servers.includes(x.id) : x.ok && x.downloads);
        box.innerHTML = found.length ? found.map((x) => serverRow(x, pick(x))).join('') : `<p class="help">${t('set.noServers')}</p>`;
      } catch (e) { box.innerHTML = `<p>${res(false, e.message)}</p>`; }
    };
    $('#w-signin').onclick = () => {
      $('#w-signin-res').innerHTML = `<span class="spinner"></span> ${t('wiz.plex.waiting')}`;
      stopSignIn = plexSignIn(async (r) => {
        if (!r.ok) { $('#w-signin-res').innerHTML = res(false, r.error); return; }
        $('#w-signin-res').innerHTML = res(true, t('wiz.plex.signedIn'));
        await reload(); $('[data-k=plex_token]').value = s.plex_token;
        list();
      });
    };
    $('#w-find').onclick = () => list($('[data-k=plex_token]').value.trim());
    if (s.plex_token) list();
  }

  if (name === 'arr') {
    v.innerHTML = frame(['radarr', 'sonarr'].map((a) => `<div class="conn">
      <div class="conn-h"><b>${a === 'radarr' ? 'Radarr' : 'Sonarr'}</b><span class="muted">${a === 'radarr' ? t('set.movies') : t('set.series')}</span><span id="w-${a}-res"></span></div>
      <div class="form">${field(a + '_url', 'URL', s[a + '_url'], { ph: a === 'radarr' ? 'http://radarr:7878' : 'http://sonarr:8989' })}${field(a + '_api_key', t('set.apiKey'), s[a + '_api_key'])}</div>
    </div>`).join(''));
  }

  if (name === 'folder') {
    v.innerHTML = frame(`
      <dl class="kv card" style="box-shadow:none"><dt>${t('wiz.folder.dir')}</dt><dd class="mono">${esc(s.downloadDir)}</dd></dl>
      <div class="checks" id="w-seen"></div>
      <div class="actions"><button class="btn sm" id="w-recheck">${I.refresh} ${t('wiz.folder.check')}</button></div>
      <details class="wiz-more"><summary>${t('wiz.folder.example')}</summary><p class="help">${t('wiz.folder.exampleText')}</p></details>`);
    const check = async () => {
      $('#w-seen').innerHTML = `<div class="srow"><span class="spinner"></span> ${t('wiz.folder.checking')}</div>`;
      const r = await post('/api/check/download-dir');
      $('#w-seen').innerHTML = Object.entries(r.apps).map(([a, x]) => {
        const app = a === 'radarr' ? 'Radarr' : 'Sonarr';
        return `<div class="check-row"><div><b>${app}</b><div>${x.ok ? res(true, t('wiz.folder.seen'))
          : res(false, x.error || t('wiz.folder.notSeen', { dir: r.dir }))}</div></div></div>`;
      }).join('');
      return r;
    };
    $('#w-recheck').onclick = check;
    check();
  }

  if (name === 'link') {
    const st = await api('/api/status');
    v.innerHTML = frame(`
      <div class="form">${field('self_url', t('set.selfUrl'), s.self_url, { ph: s.selfUrlDefault, help: t('wiz.link.selfUrlHelp') })}</div>
      <div id="w-links" style="margin-top:14px">${['radarr', 'sonarr'].map((a) => linkRow(a, st)).join('')}</div>`);
    $('#w-links').addEventListener('click', async (e) => {
      const b = e.target.closest('[data-setup]'); if (!b) return;
      b.disabled = true; err('');
      try {
        await put('/api/settings', { self_url: $('[data-k=self_url]').value });
        await post('/api/arr/setup', { app: b.dataset.setup, active: b.dataset.active === '1' });
        await reload();
        const st2 = await api('/api/status');
        $('#w-links').innerHTML = ['radarr', 'sonarr'].map((a) => linkRow(a, st2)).join('');
      } catch (ex) { err(ex.message); b.disabled = false; }
    });
  }

  if (name === 'behaviour') {
    v.innerHTML = frame(`
      <div class="presets" id="w-mode">
        ${['fallback', 'equal'].map((m) => `<button class="preset ${s.mode === m ? 'on' : ''}" data-mode="${m}"><b>${t('mode.' + m)}</b><span>${t('mode.' + m + '.help')}</span></button>`).join('')}
      </div>
      <div id="w-fallback" ${s.mode === 'fallback' ? '' : 'hidden'}>
        <div class="form">${field('wait_hours', t('set.waitHours'), s.wait_hours, { type: 'number', min: 0, help: t('set.waitHoursHelp') })}</div>
        ${sw('sweep_enabled', t('set.sweepOn'), s.sweep_enabled, t('set.sweepHelp'))}
      </div>`);
    $('#w-mode').addEventListener('click', (e) => {
      const b = e.target.closest('[data-mode]'); if (!b) return;
      $$('#w-mode .preset').forEach((x) => x.classList.toggle('on', x === b));
      $('#w-fallback').hidden = b.dataset.mode !== 'fallback';
    });
  }

  if (name === 'login') {
    v.innerHTML = frame(`
      ${sw('auth_on', t('set.loginOn'), s.auth_enabled || !!s.auth_user)}
      <div class="form">
        ${field('auth_user', t('login.user'), s.auth_user || 'admin')}
        <label class="ff"><span class="ff-l">${t('login.password')}</span><input class="input" id="w-pass" type="password" autocomplete="new-password" placeholder="${esc(s.auth_enabled ? t('set.unchanged') : t('wiz.login.min'))}"></label>
      </div>`, { skip: true });
  }

  if (name === 'done') {
    const [st, seen] = await Promise.all([api('/api/status'), post('/api/check/download-dir').catch(() => ({ apps: {} }))]);
    const apps = ['radarr', 'sonarr'].filter((a) => st[a]);
    const linked = apps.length && apps.every((a) => st.links[a]?.linked);
    const row = (ok, label) => `<li>${ok ? `<span class="res ok">${icon('check', 14)}</span>` : '<span class="res warn">–</span>'} ${label}</li>`;
    v.innerHTML = frame(`<ul class="wiz-summary">
        ${row(st.servers.length, t('wiz.done.servers', { n: st.servers.length }))}
        ${row(st.radarr, 'Radarr')}${row(st.sonarr, 'Sonarr')}
        ${row(apps.length && apps.every((a) => seen.apps[a]?.ok), t('wiz.s.folder'))}
        ${row(linked, t(!linked ? 'wiz.done.notLinked' : apps.some((a) => st.links[a]?.active) ? 'wiz.done.linked' : 'wiz.done.linkedOff'))}
        ${row(true, t('wiz.done.mode', { mode: t('mode.' + st.mode) }))}
        ${row(st.auth, t('set.login'))}
      </ul><p class="help">${t('wiz.done.more')}</p>`, { next: t('wiz.finish') });
  }

  // --- navigation --------------------------------------------------------------------
  $('#wiz-back')?.addEventListener('click', () => go(step - 1));
  $('#wiz-skip')?.addEventListener('click', () => go(step + 1));
  $('#wiz-next')?.addEventListener('click', async (e) => {
    const btn = e.currentTarget; btn.disabled = true; err('');
    try {
      if (await validate(name)) { await reload(); go(step + 1); }
    } catch (ex) { err(ex.message); }
    btn.disabled = false;
  });
}

async function validate(name) {
  if (name === 'plex') {
    const chosen = $$('#w-servers input[type=checkbox]:checked').map((x) => x.value);
    if (!chosen.length) { err(t('wiz.plex.need')); return false; }
    await put('/api/settings', { plex_token: $('[data-k=plex_token]').value.trim(), servers: chosen });
    return true;
  }
  if (name === 'arr') {
    const results = [];
    for (const a of ['radarr', 'sonarr']) {
      const url = $(`[data-k=${a}_url]`).value.trim(), key = $(`[data-k=${a}_api_key]`).value.trim();
      await put('/api/settings', { [`${a}_url`]: url, [`${a}_api_key`]: key });
      if (!url) continue;
      const out = $(`#w-${a}-res`); out.innerHTML = '<span class="spinner"></span>';
      const r = await post('/api/arr/test', { app: a, url, key });
      out.innerHTML = r.ok ? res(true, `${r.app} ${r.version}`) : res(false, r.error === 'api_key' ? t('set.wrongKey') : r.error);
      results.push(r.ok);
    }
    if (!results.length || results.includes(false)) { err(t(results.length ? 'wiz.arr.fail' : 'wiz.arr.need')); return false; }
    return true;
  }
  if (name === 'folder') {
    const r = await post('/api/check/download-dir');
    const bad = Object.entries(r.apps).filter(([, x]) => !x.ok).map(([a]) => (a === 'radarr' ? 'Radarr' : 'Sonarr'));
    return !bad.length || confirm(t('wiz.folder.continue', { apps: bad.join(' & ') }));
  }
  if (name === 'link') {
    await put('/api/settings', { self_url: $('[data-k=self_url]').value });
    const st = await api('/api/status');
    const linked = ['radarr', 'sonarr'].some((a) => st.links[a]?.linked);
    return linked || confirm(t('wiz.link.none'));
  }
  if (name === 'behaviour') {
    await put('/api/settings', {
      mode: $('#w-mode .preset.on').dataset.mode,
      wait_hours: +$('[data-k=wait_hours]').value, sweep_enabled: $('[data-k=sweep_enabled]').checked,
    });
    return true;
  }
  if (name === 'login') {
    const on = $('[data-k=auth_on]').checked, user = $('[data-k=auth_user]').value.trim(), pw = $('#w-pass').value;
    if (!on) { await put('/api/auth', { enabled: false }); return true; }
    if (pw && pw.length < 8) { err(t('err.short')); return false; }
    await put('/api/auth', { enabled: true, user, password: pw });
    $('#logout-btn').hidden = false;
    return true;
  }
  if (name === 'done') {
    await post('/api/setup/complete');
    S_.setupDone = true; S_.lib = null;
    location.hash = '#/';
    return false;
  }
  return true;
}

export async function renderWizard(S) {
  S_ = S;
  await reload();
  if (!S.setupDone) step = 0;
  draw();
}
