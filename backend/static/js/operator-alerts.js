// Operator emergency alarm (loaded only on operator / admin pages - never on patient devices).
//
// * The list of UNHANDLED patient requests comes from the server (operator_feed -> "alerts"): open emergencies no
//   operator has accepted yet, and pending medical-test requests. The page never decides on its own what is
//   unhandled, so refreshing, reconnecting or opening a second tab cannot create or lose an alert.
// * While at least one request is unhandled, an ambulance-style siren (static/audio/ambulance-siren.wav) plays every
//   SIREN_EVERY_MS. It stops as soon as the list is empty; accepting one request leaves the others ringing.
// * Browsers block sound until the user interacts with the page. If playback is refused, a red
//   "Enable emergency sound" button explains this; one click unlocks sound for the tab.
// * Mute silences the siren only - the red panel, counts and toasts keep working.
// * Several open tabs share one siren (a localStorage timestamp), so the sound is not doubled.
// * Updates: WebSocket nudges + polling fallback (App.live).
(function () {
  const body = document.body, FEED = body.dataset.operatorFeed;
  if (!FEED) return;
  const ACCEPT_URL = body.dataset.alertAccept, SIREN_URL = body.dataset.siren, E = App.esc;
  const SIREN_EVERY_MS = 12000;
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* private mode */ } },
  };

  // ---------------------------------------------------------------- sound
  const siren = SIREN_URL ? new Audio(SIREN_URL) : null;
  if (siren) { siren.preload = 'auto'; siren.volume = 0.9; }
  let muted = store.get('opMuted', false), blocked = false, alerts = [], known = null;

  function stopSiren() { if (siren) { siren.pause(); siren.currentTime = 0; } }

  function playSiren(force = false) {
    if (!siren || muted || !alerts.length) return;
    const now = Date.now(), last = store.get('opSirenAt', 0);
    if (!force && now - last < SIREN_EVERY_MS - 500) return;  // another tab (or this one) just played it
    store.set('opSirenAt', now);
    siren.currentTime = 0;
    const p = siren.play();
    if (p && p.catch) p.then(() => { if (blocked) { blocked = false; render(); } })
                       .catch(err => { if (err && err.name === 'NotAllowedError') { blocked = true; render(); } });
  }
  setInterval(() => playSiren(), 1000);  // checks every second, rings every SIREN_EVERY_MS

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

  function render() {
    panel.classList.toggle('d-none', !alerts.length);
    if (!alerts.length) { panel.innerHTML = ''; return; }
    const newest = alerts[alerts.length - 1].key;
    const items = alerts.slice().reverse().map(a => `
      <div class="op-item${a.key === newest ? ' newest' : ''}" data-key="${E(a.key)}">
        <div class="d-flex align-items-center gap-2">
          <span class="op-status PENDING">PENDING</span>
          <span class="op-prio ${E(a.priority)}">${E(a.priority)}</span>
          ${a.key === newest ? '<span class="badge text-bg-danger">NEW</span>' : ''}
          <span class="ms-auto op-meta">${when(a.submitted_at)}</span>
        </div>
        <div class="fw-semibold mt-1">${E(a.type)}</div>
        <div class="op-meta">${E(a.patient)} · ${E(a.patient_code)} · ${E(a.reference)}${a.status !== 'pending' ? ' · ' + E(a.status_detail) : ''}</div>
        <div class="d-flex gap-2 mt-2">
          <button type="button" class="btn btn-danger btn-sm op-accept" data-key="${E(a.key)}"><i class="bi bi-check2-circle"></i> Accept request</button>
          <a class="btn btn-outline-secondary btn-sm" href="${E(a.url)}"><i class="bi bi-eye"></i> View details</a>
        </div>
      </div>`).join('');
    panel.innerHTML = `
      <div class="op-head">
        <span class="pulse" aria-hidden="true"></span>
        <b>EMERGENCY</b><span>${alerts.length} unhandled request${alerts.length > 1 ? 's' : ''}</span>
        <button type="button" class="btn btn-sm btn-light ms-auto op-mute" aria-pressed="${muted}">
          <i class="bi ${muted ? 'bi-volume-mute-fill' : 'bi-volume-up-fill'}"></i> ${muted ? 'Unmute' : 'Mute'}</button>
      </div>
      ${blocked && !muted ? `<div class="op-enable"><b>Sound is blocked by your browser.</b> Browsers only play sound after you
        interact with the page. <button type="button" class="btn btn-sm btn-danger mt-1 w-100 op-enable-btn">
        <i class="bi bi-volume-up-fill"></i> Enable emergency sound</button></div>` : ''}
      <div class="op-list">${items}</div>`;
  }

  panel.addEventListener('click', async e => {
    if (e.target.closest('.op-mute')) return setMuted(!muted);
    if (e.target.closest('.op-enable-btn')) { blocked = false; playSiren(true); return render(); }
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
    if (muted) stopSiren(); else { blocked = false; playSiren(true); }  // the click also unlocks audio
    renderNav(); render();
  }
  navBtn && (navBtn.onclick = () => setMuted(!muted));
  // any click on the page counts as the interaction browsers require; retry right away if sound was blocked
  document.addEventListener('click', () => { if (blocked && !muted) { blocked = false; playSiren(true); render(); } }, true);

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
    let fresh = false;
    for (const a of list) {
      if (known && !known.has(a.key)) fresh = true;      // arrived while this page was open
      if (!seen.has(a.key)) { seen.add(a.key); toast(`<b><i class="bi bi-exclamation-octagon-fill"></i> New request ${E(a.reference)}</b><br>${E(a.type)} · ${E(a.patient)}`, 'alert-danger'); }
    }
    store.set('opSeenAlerts', [...seen].filter(k => keys.has(k)).concat([...seen].filter(k => !keys.has(k)).slice(-100)));
    const wasEmpty = !alerts.length;
    alerts = list; known = keys;
    render();
    if (!alerts.length) stopSiren();
    else if (fresh || wasEmpty) playSiren(true);        // a new request rings immediately

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
  App.live(refresh, {interval: 5000, slowInterval: 10000});
})();
