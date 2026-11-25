// Operator alarm: visual badge + audible notification for new patient test requests and critical alerts.
// * Sounds are generated with the Web Audio API (no audio files).
// * Browsers block audio until the user clicks: the operator presses "Enable sound" once; the choice is
//   remembered and the audio engine is resumed on the first click of every page.
// * A pending request rings every 20 s, at most 10 times, and stops as soon as it is accepted/cancelled.
// * Critical alerts use a different, faster tone and ring once each.
(function () {
  const FEED = document.body.dataset.operatorFeed;
  if (!FEED) return;
  const store = {
    get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* private mode */ } },
  };
  const REPEAT_MS = 20000, MAX_RINGS = 10;
  let ctx = null, soundOn = store.get('opSound', false);
  const badge = document.getElementById('op-badge'), btn = document.getElementById('op-sound'),
        toastBox = document.getElementById('op-toasts');

  function audio() {
    if (!ctx) { const AC = window.AudioContext || window.webkitAudioContext; if (!AC) return null; ctx = new AC(); }
    if (ctx.state === 'suspended') ctx.resume().catch(() => {});
    return ctx;
  }
  function tone(freq, start, dur, type = 'sine', vol = 0.25) {
    const a = audio(); if (!a) return;
    const o = a.createOscillator(), g = a.createGain();
    o.type = type; o.frequency.value = freq;
    g.gain.setValueAtTime(0.0001, a.currentTime + start);
    g.gain.exponentialRampToValueAtTime(vol, a.currentTime + start + 0.02);
    g.gain.exponentialRampToValueAtTime(0.0001, a.currentTime + start + dur);
    o.connect(g).connect(a.destination); o.start(a.currentTime + start); o.stop(a.currentTime + start + dur + 0.05);
  }
  const SOUNDS = {
    request() { tone(880, 0, 0.35); tone(660, 0.4, 0.5); },                       // calm two-tone chime
    critical() { for (let i = 0; i < 6; i++) tone(i % 2 ? 700 : 1050, i * 0.18, 0.16, 'square', 0.18); }, // urgent
  };
  function play(kind) {
    if (!soundOn) return false;
    const a = audio();
    if (!a || a.state !== 'running') { showUnlock(); return false; }
    SOUNDS[kind](); return true;
  }
  function showUnlock() { btn && btn.classList.add('btn-warning'); btn && (btn.title = 'Click to allow sound in this tab'); }
  function renderBtn() {
    if (!btn) return;
    btn.innerHTML = soundOn ? '<i class="bi bi-volume-up-fill"></i><span class="d-none d-md-inline"> Sound on</span>'
                            : '<i class="bi bi-volume-mute"></i><span class="d-none d-md-inline"> Enable sound</span>';
    btn.classList.toggle('btn-success', soundOn); btn.classList.toggle('btn-light', !soundOn); btn.classList.remove('btn-warning');
    btn.setAttribute('aria-pressed', String(soundOn));
  }
  btn && (btn.onclick = () => { soundOn = !soundOn; store.set('opSound', soundOn); renderBtn(); if (soundOn) { audio(); SOUNDS.request(); } });
  // any click on the page unlocks audio for this tab
  document.addEventListener('click', () => { if (soundOn) audio(); renderBtn(); }, {once: true, capture: true});

  function toast(html, cls) {
    if (!toastBox) return;
    const el = document.createElement('div');
    el.className = `alert ${cls} shadow-sm py-2 px-3 mb-2 small`; el.innerHTML = html;
    toastBox.prepend(el); setTimeout(() => el.remove(), 15000);
  }

  async function refresh() {
    const r = await fetch(FEED, {credentials: 'same-origin'});
    if (!r.ok) return;
    const d = await r.json(), E = App.esc, now = Date.now();
    if (badge) { badge.textContent = d.pending_count; badge.classList.toggle('d-none', !d.pending_count); }
    document.querySelectorAll('[data-pending-count]').forEach(el => { el.textContent = d.pending_count; el.classList.toggle('d-none', !d.pending_count); });

    // ring for pending requests until accepted (max MAX_RINGS each)
    const rings = store.get('opRings', {}), live = new Set(d.pending.map(p => p.reference));
    let ring = false;
    for (const p of d.pending) {
      const st = rings[p.reference] || {n: 0, last: 0};
      if (st.n === 0) toast(`<b><i class="bi bi-clipboard2-pulse"></i> New test request ${E(p.reference)}</b><br>${E(p.patient)} (${E(p.patient_code)}) · ${E(p.tests)}${p.emergency ? ' · emergency ' + E(p.emergency) : ''}<br><a href="${document.body.dataset.testRequestsUrl}${E(p.reference)}/" class="alert-link">Open request</a>`, 'alert-primary');
      if (st.n < MAX_RINGS && now - st.last >= REPEAT_MS) { ring = true; st.n++; st.last = now; }
      rings[p.reference] = st;
    }
    for (const ref of Object.keys(rings)) if (!live.has(ref)) delete rings[ref]; // accepted / cancelled: never ring again
    store.set('opRings', rings);

    // critical alerts: different sound, once per alert
    const seen = new Set(store.get('opCritical', [])); let crit = false;
    for (const c of d.critical) if (!seen.has(c.id)) { seen.add(c.id); crit = true; toast(`<b><i class="bi bi-exclamation-triangle-fill"></i> CRITICAL</b> ${E(c.reference)}<br>${E(c.message)}`, 'alert-danger'); }
    store.set('opCritical', [...seen].slice(-200));

    if (crit) play('critical'); else if (ring) play('request');
    document.querySelectorAll('.pending-row').forEach(tr => tr.classList.toggle('table-warning', live.has(tr.dataset.ref)));
  }
  renderBtn();
  App.live(refresh, {interval: 5000, slowInterval: 10000});
})();
