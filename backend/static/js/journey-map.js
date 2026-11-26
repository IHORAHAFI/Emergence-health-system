// SIMULATED ambulance journey on a Leaflet map: hospital -> patient -> hospital along road routes.
// The server is the source of truth (status, route, timing). Between server updates the marker is moved
// smoothly with requestAnimationFrame using the SAME formula the server uses (route + elapsed time), so it never
// jumps or drives through buildings, and a refresh simply continues from the server's state.
//
//   JourneyMap.attach(map, {url, panel, controls, onChange})
//     url      endpoint returning the journey state (or null) - /api/journeys/by-emergency/<ref>/
//     panel    element for the journey information panel
//     controls true for operators (pause / resume / reset / retry / cancel / confirm location)
(function () {
  const COLORS = {to_patient: '#e03131', to_hospital: '#1d4ed8', done: '#868e96'};
  const LABEL = {
    WAITING_LOCATION: ['Waiting for patient location', 'warning'], ROUTING: ['Calculating road route', 'secondary'],
    EN_ROUTE_TO_PATIENT: ['En route to patient', 'primary'], ARRIVED_AT_PATIENT: ['Arrived at patient', 'success'],
    PICKING_UP_PATIENT: ['Picking up patient', 'info'], PATIENT_ON_BOARD: ['Patient on board', 'info'],
    EN_ROUTE_TO_HOSPITAL: ['Transporting to hospital', 'primary'],
    ARRIVED_AT_HOSPITAL: ['Arrived at hospital - completed', 'success'],
    FAILED: ['Stopped - needs attention', 'danger'], CANCELLED: ['Cancelled', 'secondary'],
  };
  const E = s => (window.App && App.esc) ? App.esc(s) : String(s ?? '').replace(/[&<>"']/g, c => `&#${c.charCodeAt(0)};`);
  const km = m => m == null ? '-' : (m >= 1000 ? (m / 1000).toFixed(1) + ' km' : Math.round(m) + ' m');
  const mins = s => s == null ? '-' : (s < 60 ? Math.round(s) + ' s' : Math.round(s / 60) + ' min');

  function ambIcon(deg) {
    return L.divIcon({className: '', iconSize: [34, 34], iconAnchor: [17, 17],
      html: `<div style="width:34px;height:34px;border-radius:50%;background:#fff;border:2px solid #e8590c;display:flex;
        align-items:center;justify-content:center;box-shadow:0 1px 4px rgba(0,0,0,.35)">
        <i class="bi bi-truck-front-fill" style="font-size:18px;color:#e8590c"></i></div>
        <div style="position:absolute;left:13px;top:-9px;transform-origin:4px 26px;transform:rotate(${deg || 0}deg)">
        <div style="width:0;height:0;border-left:4px solid transparent;border-right:4px solid transparent;
        border-bottom:8px solid #e8590c"></div></div>`});
  }

  function pointAt(leg, frac) {  // same maths as emergency/journey.py point_at()
    const c = leg.coords, cum = leg.cum_m, target = Math.min(Math.max(frac, 0), 1) * cum[cum.length - 1];
    let lo = 0, hi = cum.length - 1;
    while (lo < hi - 1) { const mid = (lo + hi) >> 1; if (cum[mid] <= target) lo = mid; else hi = mid; }
    const seg = cum[hi] - cum[lo], t = seg > 0 ? (target - cum[lo]) / seg : 0;
    const a = c[lo], b = c[hi];
    const y = Math.sin((b[1] - a[1]) * Math.PI / 180) * Math.cos(b[0] * Math.PI / 180);
    const x = Math.cos(a[0] * Math.PI / 180) * Math.sin(b[0] * Math.PI / 180)
      - Math.sin(a[0] * Math.PI / 180) * Math.cos(b[0] * Math.PI / 180) * Math.cos((b[1] - a[1]) * Math.PI / 180);
    return {pos: [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t], deg: (Math.atan2(y, x) * 180 / Math.PI + 360) % 360};
  }

  function attach(map, opts) {
    const layers = {}, panel = opts.panel;
    let st = null, recvAt = 0, raf = null, fitted = false, lastDeg = 0, busy = false;

    function marker(key, pos, icon, popup) {
      if (!pos) { if (layers[key]) { map.removeLayer(layers[key]); delete layers[key]; } return; }
      if (!layers[key]) layers[key] = L.marker(pos, {icon, zIndexOffset: key === 'amb' ? 1000 : 0}).addTo(map);
      const m = layers[key], ik = icon.options.html;
      m.setLatLng(pos);
      if (m._ik !== ik) { m.setIcon(icon); m._ik = ik; }   // only rebuild the icon when it really changed
      if (popup && m._pp !== popup) { m.bindPopup(popup); m._pp = popup; }
    }
    function line(key, coords, style) {
      if (!coords) { if (layers[key]) { map.removeLayer(layers[key]); delete layers[key]; } return; }
      if (!layers[key]) layers[key] = L.polyline(coords, style).addTo(map); else layers[key].setLatLngs(coords).setStyle(style);
    }

    function draw() {
      const s = st;
      if (!s) { Object.keys(layers).forEach(k => { map.removeLayer(layers[k]); delete layers[k]; }); return; }
      const sh = s.start_hospital, dh = s.destination_hospital;
      marker('start', sh && sh.pos, App.icon('bi-hospital-fill', '#1d4ed8'), sh && `<b>${E(sh.name)}</b><br>Ambulance base (start)`);
      const sameHosp = sh && dh && sh.pos[0] === dh.pos[0] && sh.pos[1] === dh.pos[1];
      marker('dest', dh && !sameHosp && dh.pos, App.icon('bi-hospital-fill', '#7048e8'), dh && `<b>${E(dh.name)}</b><br>Destination hospital`);
      const onBoard = ['PATIENT_ON_BOARD', 'EN_ROUTE_TO_HOSPITAL', 'ARRIVED_AT_HOSPITAL'].includes(s.status);
      marker('pickup', s.pickup, App.icon(onBoard ? 'bi-geo-alt' : 'bi-person-fill', onBoard ? '#868e96' : '#c92a2a'),
        `<b>Patient pickup location</b><br>${s.pickup ? s.pickup[0].toFixed(5) + ', ' + s.pickup[1].toFixed(5) : ''}`);
      const r = s.routes || {};
      const outbound = s.status === 'EN_ROUTE_TO_PATIENT';
      line('r1', r.to_patient, {color: outbound ? COLORS.to_patient : COLORS.done, weight: outbound ? 5 : 3, opacity: .8, dashArray: outbound ? null : '6'});
      line('r2', r.to_hospital, {color: s.status === 'EN_ROUTE_TO_HOSPITAL' ? COLORS.to_hospital : COLORS.done, weight: 5, opacity: .8});
      if (!fitted) {
        const pts = [sh && sh.pos, dh && dh.pos, s.pickup, s.position].filter(Boolean).concat(r.to_patient || [], r.to_hospital || []);
        if (pts.length > 1) { map.fitBounds(pts, {padding: [30, 30]}); fitted = true; }
      }
    }

    function frame() {  // smooth movement between server updates; never past the end of the leg
      raf = null;
      if (!st || !st.position) { marker('amb', null); return; }
      let pos = st.position, deg = st.bearing ?? lastDeg, leg = st.leg, el = null;
      if (leg && !st.paused) {
        el = Math.min(leg.sim_elapsed_s + (performance.now() - recvAt) / 1000, leg.sim_duration_s);
        const p = pointAt(leg, el / leg.sim_duration_s); pos = p.pos; deg = p.deg;
      }
      lastDeg = deg;
      marker('amb', pos, ambIcon(Math.round(deg / 5) * 5), `<b>${E(st.ambulance.plate)}</b> <span class="badge badge-sim">SIMULATED</span><br>${E((LABEL[st.status] || [st.status])[0])}`);
      if (leg) {  // traveled part of the active route
        const done = [], frac = (el ?? leg.sim_elapsed_s) / leg.sim_duration_s, target = frac * leg.cum_m[leg.cum_m.length - 1];
        for (let i = 0; i < leg.coords.length && leg.cum_m[i] <= target; i++) done.push(leg.coords[i]);
        done.push(pos);
        line('trail', done, {color: '#495057', weight: 5, opacity: .55});
        renderLive(frac);
        if (!st.paused && el < leg.sim_duration_s) raf = requestAnimationFrame(frame);
      } else line('trail', null);
    }

    function renderLive(frac) {
      const leg = st.leg; if (!leg || !panel) return;
      const set = (id, v) => { const el = panel.querySelector(`[data-j="${id}"]`); if (el) el.textContent = v; };
      set('remaining', km(leg.distance_m * (1 - frac)));
      set('eta_real', mins(leg.duration_s * (1 - frac)));
      set('eta_sim', mins(leg.sim_duration_s * (1 - frac)));
      const bar = panel.querySelector('[data-j="bar"]'); if (bar) bar.style.width = (frac * 100).toFixed(1) + '%';
    }

    function renderPanel() {
      if (!panel) return;
      const s = st;
      if (!s) { panel.innerHTML = '<div class="small text-muted">No simulated ambulance journey for this request.</div>' + (opts.controls && opts.canStart ? '<button class="btn btn-sm btn-outline-primary mt-1" data-act="start"><i class="bi bi-play-fill"></i> Start simulated journey</button>' : ''); bind(); return; }
      const [label, color] = LABEL[s.status] || [s.status, 'secondary'];
      const leg = s.leg, toPatient = s.status === 'EN_ROUTE_TO_PATIENT' || ['WAITING_LOCATION', 'ROUTING'].includes(s.status);
      const dest = toPatient ? "Patient's location" : (s.destination_hospital ? s.destination_hospital.name : '-');
      const rows = [
        ['Ambulance', E(s.ambulance.plate)],
        ['Journey status', `<span class="badge bg-${color}">${E(label)}</span> <code class="small">${E(s.status)}</code>${s.paused ? ' <span class="badge bg-warning text-dark">PAUSED</span>' : ''}`],
        ['Pickup location', s.pickup ? `Patient's location (${s.pickup[0].toFixed(5)}, ${s.pickup[1].toFixed(5)})` : '<span class="text-danger">not available</span>'],
        ['Heading to', E(dest)],
        ['Destination hospital', s.destination_hospital ? E(s.destination_hospital.name) : '-'],
        ['Remaining distance', leg ? `<span data-j="remaining">${km(leg.remaining_m)}</span> <span class="text-muted small">(road route)</span>` : '-'],
        ['Estimated arrival', leg ? `<span data-j="eta_real">${mins(leg.remaining_real_s)}</span> <span class="text-muted small">real-world estimate from the routing service (no live traffic)</span>` : '-'],
        ['Simulated arrival', leg ? `<span data-j="eta_sim">${mins(leg.sim_remaining_s)}</span> <span class="text-muted small">marker runs ${s.sim_speedup}x faster</span>` : (s.pickup_remaining_s != null ? `pickup ends in ${mins(s.pickup_remaining_s)}` : '-')],
        ['Simulation status', '<span class="badge badge-sim">SIMULATED</span> not a real vehicle position'],
      ];
      panel.innerHTML = `<table class="table table-sm small mb-1"><tbody>${rows.map(r => `<tr><th class="text-nowrap">${r[0]}</th><td>${r[1]}</td></tr>`).join('')}</tbody></table>`
        + (leg ? '<div class="progress mb-1" style="height:6px"><div class="progress-bar" data-j="bar" style="width:0"></div></div>' : '')
        + (s.error ? `<div class="alert alert-${s.status === 'FAILED' || s.status === 'WAITING_LOCATION' ? 'warning' : 'secondary'} py-1 px-2 small mb-1">${E(s.error)}</div>` : '')
        + (s.routing_source ? `<div class="text-muted" style="font-size:11px">Road route: ${E(s.routing_source)}</div>` : '')
        + (opts.controls ? controls(s) : '') + '<div class="small text-danger" data-j="msg"></div>';
      bind();
    }

    function controls(s) {
      const b = (act, icon, text, cls = 'outline-secondary') => `<button class="btn btn-sm btn-${cls}" data-act="${act}"><i class="bi ${icon}"></i> ${text}</button>`;
      const out = [], running = ['EN_ROUTE_TO_PATIENT', 'EN_ROUTE_TO_HOSPITAL', 'PICKING_UP_PATIENT', 'ARRIVED_AT_PATIENT'].includes(s.status);
      if (!s.is_open) return '';
      if (running && !s.paused) out.push(b('pause', 'bi-pause-fill', 'Pause'));
      if (s.paused) out.push(b('resume', 'bi-play-fill', 'Resume', 'primary'));
      if (s.status === 'FAILED') out.push(b('retry', 'bi-arrow-repeat', 'Retry', 'primary'));
      if (['WAITING_LOCATION', 'ROUTING', 'EN_ROUTE_TO_PATIENT', 'FAILED'].includes(s.status) && !s.times.pickup_completed_at) out.push(b('reset', 'bi-skip-backward-fill', 'Reset'));
      out.push(b('fit', 'bi-arrows-fullscreen', 'View full journey'));
      out.push(b('cancel', 'bi-x-circle', 'Cancel simulation', 'outline-danger'));
      let html = `<div class="d-flex flex-wrap gap-1 mt-1">${out.join('')}</div>`;
      if (['WAITING_LOCATION', 'FAILED'].includes(s.status) && !s.routes.to_patient) html += `<form class="d-flex gap-1 mt-1" data-j="locform">
        <input class="form-control form-control-sm" name="latitude" placeholder="Latitude" required>
        <input class="form-control form-control-sm" name="longitude" placeholder="Longitude" required>
        <button class="btn btn-sm btn-warning text-nowrap">Confirm location</button></form>
        <div class="text-muted" style="font-size:11px">Only after confirming the position with the patient (e.g. by phone). Tip: click the map to fill it in.</div>`;
      return html;
    }

    async function post(url, body) {
      const r = await fetch(url, {method: 'POST', credentials: 'same-origin',
        headers: {'Content-Type': 'application/json', 'X-CSRFToken': App.csrf()}, body: JSON.stringify(body)});
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.detail || 'Request failed');
      return d;
    }

    function bind() {
      if (!panel) return;
      const msg = t => { const m = panel.querySelector('[data-j="msg"]'); if (m) m.textContent = t || ''; };
      panel.querySelectorAll('[data-act]').forEach(btn => btn.onclick = async () => {
        const act = btn.dataset.act;
        if (act === 'fit') { fitted = false; draw(); return; }
        if (act === 'cancel' && !confirm('Cancel the simulated journey? The emergency itself is not changed.')) return;
        btn.disabled = true;
        try {
          const d = act === 'start' ? await post('/api/journeys/start/', {emergency: opts.reference})
            : await post(`/api/journeys/${st.id}/action/`, {action: act});
          apply(d);
        } catch (e) { msg(e.message); btn.disabled = false; }
      });
      const f = panel.querySelector('[data-j="locform"]');
      if (f) f.onsubmit = async ev => {
        ev.preventDefault();
        try { apply(await post(`/api/journeys/${st.id}/pickup-location/`, {latitude: f.latitude.value, longitude: f.longitude.value})); }
        catch (e) { msg(e.message); }
      };
    }
    if (opts.controls) map.on('click', e => {  // fill the "confirm location" form from the map
      const f = panel && panel.querySelector('[data-j="locform"]');
      if (f) { f.latitude.value = e.latlng.lat.toFixed(6); f.longitude.value = e.latlng.lng.toFixed(6); }
    });

    function apply(d) {
      const prev = st && st.status;
      st = d || null; recvAt = performance.now();
      draw(); renderPanel();
      if (raf) cancelAnimationFrame(raf);
      frame();
      if (opts.onChange && prev !== (st && st.status)) opts.onChange(st, prev);
    }

    async function refresh() {
      if (busy) return; busy = true;
      try {
        const r = await fetch(opts.url, {credentials: 'same-origin'});
        if (r.ok) apply(await r.json());
      } finally { busy = false; }
    }
    // WebSocket nudges (Django Channels) + polling fallback; the server state is re-read on every reconnect
    App.live(refresh, {interval: 3000, slowInterval: 6000});
    return {refresh, get state() { return st; }};
  }

  window.JourneyMap = {attach};
})();
