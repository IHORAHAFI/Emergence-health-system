// Operator emergency alarm (loaded only on operator / admin pages - never on patient devices).
//
// * The SERVER is the source of truth (operator_feed -> "alerts"): every unhandled patient service request
//   (emergency, medical test, request for a professional, symptom / caregiver report - from web or mobile),
//   each with its OWN alert_count (1 on arrival, 2 and 3 every ALERT_INTERVAL) and AI-takeover state.
//   Refreshing, reconnecting or opening a second tab cannot create, replay or lose an alert.
// * Siren: the verified Police Wail file (emergency/siren.py). The page plays the WHOLE file once each time a
//   request's alert_count goes up (max 3 per request) - never continuously.
// * Browsers only allow sound after the operator interacts with the page: an "Enable emergency sound" bar is shown
//   until it is clicked once in this tab. Every playback failure is shown in red (missing file, load error,
//   blocked autoplay) - nothing fails silently. Visual alerts always keep working.
// * Mute silences the siren only. Several tabs share one siren (localStorage), so the sound is not doubled.
// * Updates: WebSocket nudges + polling fallback (App.live).
(function () {
  const body = document.body, FEED = body.dataset.operatorFeed;
  if (!FEED) return;
  const ACCEPT_URL = body.dataset.alertAccept, SIREN_URL = body.dataset.siren, E = App.esc;
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* private mode */ } },
  };
  const session = {
    get(k) { try { return sessionStorage.getItem(k) === '1'; } catch (e) { return false; } },
    set(k) { try { sessionStorage.setItem(k, '1'); } catch (e) { /* ignore */ } },
  };

  // ---------------------------------------------------------------- sound
  // One Audio object for the whole page (never recreated). soundState: 'locked' (needs a click), 'ready',
  // 'blocked' (browser refused), 'error' (file missing / cannot load). soundProblem explains errors.
  let soundState = SIREN_URL ? (session.get('opSoundUnlocked') ? 'ready' : 'locked') : 'error';
  let soundProblem = SIREN_URL ? '' : (body.dataset.sirenProblem || 'Siren file is not installed on the server.');
  const siren = SIREN_URL ? new Audio(SIREN_URL) : null;
  if (siren) {
    siren.preload = 'auto';
    siren.volume = 1.0;
    siren.addEventListener('error', () => {
      const code = siren.error ? siren.error.code : 0;  // 2 network, 3 decode, 4 not supported / not found
      soundState = 'error';
      soundProblem = code === 4 ? 'The siren file could not be loaded (not found or unsupported format).'
        : code === 3 ? 'The siren file is damaged and cannot be decoded.'
        : 'The siren file could not be downloaded (network error).';
      render();
    });
  }
  let muted = store.get('opMuted', false), alerts = [];

  function stopSiren() { if (siren) { siren.pause(); siren.currentTime = 0; } }

  // Must run inside a click: browsers then allow this tab to play sound later without another click.
  function unlockSound() {
    if (!siren) return;
    siren.muted = true;
    const p = siren.play();
    const done = ok => {
      siren.pause(); siren.currentTime = 0; siren.muted = false;
      if (ok) { soundState = 'ready'; session.set('opSoundUnlocked'); soundProblem = ''; playSiren(); }
      render();
    };
    if (p && p.then) p.then(() => done(true)).catch(err => {
      soundState = err && err.name === 'NotAllowedError' ? 'blocked' : 'error';
      soundProblem = soundState === 'blocked' ? 'The browser still blocks sound for this page.' : `Siren cannot play: ${err && err.message}`;
      siren.muted = false; render();
    });
    else done(true);
  }

  function owed() {  // requests whose latest server-side alert has not been played yet (max 3 each)
    const played = store.get('opPlayed', {});
    return alerts.filter(a => Math.min(a.alert_count || 1, 3) > (played[a.key] || 0));
  }
  function markPlayed(list, delta = 0) {
    const played = store.get('opPlayed', {}), live = new Set(alerts.map(a => a.key));
    for (const a of list) played[a.key] = Math.min(a.alert_count || 1, 3) + delta;
    for (const k of Object.keys(played)) if (!live.has(k)) delete played[k];  // handled requests are forgotten
    store.set('opPlayed', played);
  }

  function playSiren() {
    const due = owed();
    if (!due.length) return;
    if (muted || !siren || soundState === 'error') { markPlayed(due); return; }  // visual alert only
    if (soundState !== 'ready') { render(); return; }                           // waiting for "Enable sound"
    markPlayed(due);                                       // claim it first so another tab does not play it too
    siren.currentTime = 0;
    const p = siren.play();                                // the complete 6.97 s file, once
    if (p && p.catch) p.catch(err => {
      markPlayed(due, -1);                                 // give the alert back so it can be played after the fix
      if (err && err.name === 'NotAllowedError') { soundState = 'blocked'; soundProblem = 'The browser blocked the siren.'; }
      else if (err && err.name !== 'AbortError') { soundState = 'error'; soundProblem = `Siren cannot play: ${err.message || err.name}`; }
      render();
    });
  }
  // keep the "next alert in" countdowns current without redrawing the panel (a redraw could swallow a click)
  setInterval(() => document.querySelectorAll('#op-alarm .op-next').forEach(el => { el.textContent = countdown(el.dataset.at); }), 1000);

  // soft tone for critical vital-sign alerts (Web Audio, no file)
  let ctx = null;
  function criticalTone() {
    if (muted) return;
    try {
      ctx = ctx || new (window.AudioContext || window.webkitAudioContext)();
      for (let i = 0; i < 6; i++) {
        const o = ctx.createOscillator(), g = ctx.createGain(), t = ctx.currentTime + i * 0.18;
        o.type = 'square'; o.frequency.value = i % 2 ? 700 : 1050;
        g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(0.15, t + 0.02);
        g.gain.exponentialRampToValueAtTime(0.0001, t + 0.16);
        o.connect(g).connect(ctx.destination); o.start(t); o.stop(t + 0.2);
      }
    } catch (e) { /* no audio */ }
  }

  // ---------------------------------------------------------------- panel
  const panel = document.createElement('section');
  panel.id = 'op-alarm';
  panel.setAttribute('role', 'alert');
  panel.setAttribute('aria-live', 'assertive');
  panel.className = 'd-none';
  body.appendChild(panel);
  const style = document.createElement('style');
  style.textContent = `
    #op-alarm{position:fixed;z-index:1090;right:16px;bottom:16px;width:min(440px,calc(100vw - 32px));max-height:70vh;
      display:flex;flex-direction:column;border-radius:14px;overflow:hidden;box-shadow:0 10px 30px rgba(0,0,0,.35);
      background:#fff;color:#1d2939;border:2px solid #d92d20}
    #op-alarm.d-none{display:none!important}
    #op-alarm .op-head{background:#d92d20;color:#fff;padding:.6rem .8rem;display:flex;align-items:center;gap:.5rem}
    #op-alarm .op-head .pulse{width:12px;height:12px;border-radius:50%;background:#fff;animation:opPulse 1s infinite}
    @keyframes opPulse{0%{opacity:1}50%{opacity:.2}100%{opacity:1}}
    #op-alarm .op-list{overflow-y:auto}
    #op-alarm .op-item{padding:.6rem .8rem;border-top:1px solid #eaecf0}
    #op-alarm .op-item.newest{background:#fef3f2}
    #op-alarm .op-meta{font-size:.8rem;color:#475467}
    #op-alarm .op-enable{background:#fffaeb;border-top:1px solid #fedf89;padding:.6rem .8rem;font-size:.85rem}
    .op-status{font-size:.7rem;font-weight:700;letter-spacing:.04em;padding:.15rem .45rem;border-radius:999px}
    .op-status.PENDING{background:#fee4e2;color:#b42318}.op-status.ACCEPTED{background:#d1e9ff;color:#175cd3}
    .op-status.IN-PROGRESS{background:#fef0c7;color:#b54708}.op-status.COMPLETED{background:#d1fadf;color:#027a48}
    .op-prio{font-size:.7rem;font-weight:700;text-transform:uppercase}
    .op-prio.critical{color:#b42318}.op-prio.high{color:#c4320a}.op-prio.medium{color:#b54708}.op-prio.low{color:#027a48}
    @media (max-width:576px){#op-alarm{right:8px;left:8px;bottom:8px;width:auto}}`;
  document.head.appendChild(style);

  function when(iso) { const d = new Date(iso); return d.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'}); }
  function countdown(iso) {
    if (!iso) return '';
    const s = Math.max(0, Math.round((new Date(iso) - Date.now()) / 1000));
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
  }

  function stageLine(a) {
    const n = Math.min(a.alert_count || 1, 3);
    if (a.ai_takeover) return '<b>AI TAKEOVER</b> - patient answered by the assistant; waiting for a human';
    if (a.stage === 'takeover_due') return `Alert 3 of 3 · AI takeover in <span class="op-next" data-at="${E(a.takeover_at)}">${countdown(a.takeover_at)}</span>`;
    return `Alert ${n} of 3` + (a.next_alert_at ? ` · next in <span class="op-next" data-at="${E(a.next_alert_at)}">${countdown(a.next_alert_at)}</span>` : '');
  }

  // Always-visible sound status (also when nothing is pending, so sound can be enabled BEFORE a request arrives).
  const soundBar = document.createElement('div');
  soundBar.id = 'op-soundbar';
  soundBar.setAttribute('role', 'status');
  body.appendChild(soundBar);
  const sbStyle = document.createElement('style');
  sbStyle.textContent = `#op-soundbar{position:fixed;left:16px;bottom:16px;z-index:1089;max-width:min(380px,calc(100vw - 32px))}
    #op-soundbar .sb{border-radius:12px;padding:.5rem .75rem;font-size:.85rem;box-shadow:0 4px 14px rgba(0,0,0,.2)}
    @media (max-width:576px){#op-soundbar{left:8px;right:8px;bottom:auto;top:64px;max-width:none}}`;
  document.head.appendChild(sbStyle);

  function renderSound() {
    let html = '';
    if (soundState === 'error') {
      html = `<div class="sb bg-danger text-white"><i class="bi bi-exclamation-octagon-fill"></i> <b>Emergency siren unavailable.</b>
        ${E(soundProblem)} Visual alerts still work.</div>`;
    } else if (muted) {
      html = `<div class="sb bg-warning text-dark"><i class="bi bi-volume-mute-fill"></i> Emergency sound <b>muted</b> - alerts still show.
        <button type="button" class="btn btn-sm btn-dark ms-1 sb-unmute">Unmute</button></div>`;
    } else if (soundState === 'locked' || soundState === 'blocked') {
      html = `<div class="sb bg-danger text-white"><b>${soundState === 'blocked' ? 'Sound was blocked by the browser.' : 'Emergency sound is off.'}</b>
        Browsers only play sound after you click on the page.
        <button type="button" class="btn btn-light btn-sm w-100 mt-1 fw-bold sb-enable"><i class="bi bi-volume-up-fill"></i> Enable emergency sound</button></div>`;
    } else {
      html = `<div class="sb bg-success text-white"><i class="bi bi-volume-up-fill"></i> Emergency sound <b>on</b>
        <button type="button" class="btn btn-sm btn-light ms-1 sb-test">Test</button></div>`;
    }
    soundBar.innerHTML = html;
  }
  soundBar.addEventListener('click', e => {
    if (e.target.closest('.sb-enable')) unlockSound();
    else if (e.target.closest('.sb-unmute')) setMuted(false);
    else if (e.target.closest('.sb-test') && siren) {
      siren.currentTime = 0;
      siren.play().catch(err => { soundState = 'error'; soundProblem = `Siren cannot play: ${err.message || err.name}`; render(); });
    }
  });

  function render() {
    renderSound();
    panel.classList.toggle('d-none', !alerts.length);
    if (!alerts.length) { panel.innerHTML = ''; return; }
    const newest = alerts[alerts.length - 1].key;
    const items = alerts.slice().reverse().map(a => `
      <div class="op-item${a.key === newest ? ' newest' : ''}" data-key="${E(a.key)}">
        <div class="d-flex align-items-center gap-2 flex-wrap">
          <span class="op-status PENDING">PENDING</span>
          <span class="op-prio ${E(a.priority)}">${E(a.priority)}</span>
          ${a.key === newest ? '<span class="badge text-bg-danger">NEW</span>' : ''}
          ${a.severe ? '<span class="badge text-bg-danger">SEVERE</span>' : ''}
          ${a.ai_takeover ? '<span class="badge text-bg-dark">AI TAKEOVER</span>' : ''}
          <span class="ms-auto op-meta">${when(a.submitted_at)}</span>
        </div>
        <div class="op-meta">${stageLine(a)}</div>
        <div class="fw-semibold mt-1">${E(a.type)}</div>
        ${a.detail ? `<div class="op-meta">${E(a.detail)}</div>` : ''}
        <div class="op-meta">Patient: ${E(a.patient)} · ${E(a.patient_code)} · ${E(a.reference)}${a.status !== 'pending' ? ' · ' + E(a.status_detail) : ''}</div>
        <div class="d-flex gap-2 mt-2">
          <button type="button" class="btn btn-danger btn-sm op-accept" data-key="${E(a.key)}"><i class="bi bi-check2-circle"></i> Accept / handle</button>
          <a class="btn btn-outline-secondary btn-sm" href="${E(a.url)}"><i class="bi bi-eye"></i> View details</a>
        </div>
      </div>`).join('');
    panel.innerHTML = `
      <div class="op-head">
        <span class="pulse" aria-hidden="true"></span>
        <b>NEW PATIENT REQUEST${alerts.length > 1 ? 'S' : ''}</b><span>${alerts.length} pending</span>
        <button type="button" class="btn btn-sm btn-light ms-auto op-mute" aria-pressed="${muted}">
          <i class="bi ${muted ? 'bi-volume-mute-fill' : 'bi-volume-up-fill'}"></i> ${muted ? 'Unmute' : 'Mute'}</button>
      </div>
      <div class="op-list">${items}</div>`;
  }

  panel.addEventListener('click', async e => {
    if (e.target.closest('.op-mute')) return setMuted(!muted);
    const btn = e.target.closest('.op-accept');
    if (!btn) return;
    btn.disabled = true;
    try {
      const r = await fetch(ACCEPT_URL, {method: 'POST', credentials: 'same-origin',
        headers: {'Content-Type': 'application/json', 'X-CSRFToken': App.csrf()}, body: JSON.stringify({key: btn.dataset.key})});
      const d = await r.json();
      if (!r.ok) { toast(E(d.error || 'Could not accept the request.'), 'alert-warning'); btn.disabled = false; return; }
      apply(d);
    } catch (err) { btn.disabled = false; toast('Network error - try again.', 'alert-warning'); }
  });

  // navbar sound button = same mute switch
  const navBtn = document.getElementById('op-sound');
  function renderNav() {
    if (!navBtn) return;
    navBtn.innerHTML = muted ? '<i class="bi bi-volume-mute-fill"></i><span class="d-none d-md-inline"> Muted</span>'
                             : '<i class="bi bi-volume-up-fill"></i><span class="d-none d-md-inline"> Sound on</span>';
    navBtn.classList.toggle('btn-warning', muted); navBtn.classList.toggle('btn-light', !muted);
    navBtn.setAttribute('aria-pressed', String(!muted));
    navBtn.title = muted ? 'Emergency sound is muted (alerts still show)' : 'Mute emergency sound';
  }
  function setMuted(v) {
    muted = v; store.set('opMuted', v);
    if (muted) stopSiren(); else if (soundState !== 'ready') unlockSound();  // this click can unlock audio
    renderNav(); render();
  }
  navBtn && (navBtn.onclick = () => setMuted(!muted));
  // the first click anywhere on the page is the interaction browsers require: use it to unlock the siren
  document.addEventListener('click', e => {
    if (!muted && (soundState === 'locked' || soundState === 'blocked') && !e.target.closest('#op-soundbar')) unlockSound();
  }, true);

  // ---------------------------------------------------------------- toasts (once per request, even across reloads)
  const toastBox = document.getElementById('op-toasts');
  function toast(html, cls) {
    if (!toastBox) return;
    const el = document.createElement('div');
    el.className = `alert ${cls} shadow-sm py-2 px-3 mb-2 small`; el.innerHTML = html;
    toastBox.prepend(el); setTimeout(() => el.remove(), 15000);
  }

  // ---------------------------------------------------------------- data
  function apply(d) {
    const list = d.alerts || [];
    const keys = new Set(list.map(a => a.key));
    const seen = new Set(store.get('opSeenAlerts', []));
    for (const a of list) {
      if (!seen.has(a.key)) { seen.add(a.key); toast(`<b><i class="bi bi-exclamation-octagon-fill"></i> New request ${E(a.reference)}</b><br>${E(a.type)} · ${E(a.patient)}`, 'alert-danger'); }
    }
    store.set('opSeenAlerts', [...seen].filter(k => keys.has(k)).concat([...seen].filter(k => !keys.has(k)).slice(-100)));
    alerts = list;
    render();
    if (!alerts.length) stopSiren();   // every request handled: silence at once
    else playSiren();                  // plays only if some request's server-side alert count went up

    // existing badges + table highlights
    const badge = document.getElementById('op-badge');
    if (badge) { badge.textContent = d.pending_count; badge.classList.toggle('d-none', !d.pending_count); }
    document.querySelectorAll('[data-pending-count]').forEach(el => { el.textContent = d.pending_count; el.classList.toggle('d-none', !d.pending_count); });
    const pendingRefs = new Set((d.pending || []).map(p => p.reference));
    document.querySelectorAll('.pending-row').forEach(tr => tr.classList.toggle('table-warning', pendingRefs.has(tr.dataset.ref)));

    // critical vital-sign alerts: different tone, once each
    const crit = new Set(store.get('opCritical', [])); let ring = false;
    for (const c of d.critical || []) if (!crit.has(c.id)) { crit.add(c.id); ring = true; toast(`<b><i class="bi bi-exclamation-triangle-fill"></i> CRITICAL</b> ${E(c.reference)}<br>${E(c.message)}`, 'alert-danger'); }
    store.set('opCritical', [...crit].slice(-200));
    if (ring) criticalTone();
  }

  async function refresh() {
    const r = await fetch(FEED, {credentials: 'same-origin'});
    if (r.ok) apply(await r.json());
  }
  renderNav();
  render();  // show the sound status (Enable emergency sound) immediately, before any request arrives
  App.live(refresh, {interval: 5000, slowInterval: 10000});
})();
