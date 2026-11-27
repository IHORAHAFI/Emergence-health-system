// Live "IoT devices (Arduino)" panel. Data always comes from /dashboard/iot.json (the database); the /ws/notify/
// WebSocket only says "an iot reading was saved". After a reconnect the panel re-fetches, so nothing is missed.
(function () {
  const E = s => App.esc ? App.esc(s) : String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const UNIT = {C: '°C', bpm: 'bpm', '%': '%'};
  const fmtTime = iso => new Date(iso).toLocaleString([], {day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit'});
  const ago = (iso, now) => { const s = Math.max(0, Math.round((now - new Date(iso)) / 1000)); return s < 90 ? `${s} s ago` : `${Math.round(s / 60)} min ago`; };
  const srcPill = r => r.source === 'SIMULATED'
    ? '<span class="status-pill warning" title="Demonstration data - not a real patient measurement">SIMULATED</span>'
    : '<span class="status-pill success" title="From a physical sensor">REAL</span>';
  const statusPill = st => ({VALID: '<span class="status-pill success">VALID</span>',
    INVALID: '<span class="status-pill danger" title="Out of the plausible range - no value">INVALID</span>',
    SENSOR_ERROR: '<span class="status-pill danger" title="The sensor reported an error - no value">SENSOR ERROR</span>'}[st] || E(st));
  const valueText = r => r.value == null ? '<span class="text-muted">— no value</span>'
    : `<b>${r.sensor_type === 'temperature' ? Number(r.value).toFixed(1) : Math.round(r.value)}</b> ${E(UNIT[r.unit] || r.unit)}`;

  function deviceItem(d, now) {
    const conn = !d.approved ? '<span class="status-pill warning">PENDING APPROVAL</span>'
      : d.online ? '<span class="status-pill success"><i class="bi bi-plug" aria-hidden="true"></i> CONNECTED</span>'
      : '<span class="status-pill danger"><i class="bi bi-x-circle" aria-hidden="true"></i> DISCONNECTED</span>';
    const sensors = Object.entries(d.sensor_state || {}).map(([k, v]) => `${E(k)}: ${E(v)}`).join(' · ');
    return `<li class="list-group-item small d-flex flex-wrap gap-2 align-items-center">
      <span class="fw-semibold">${E(d.name)}</span>${conn}
      <span class="status-pill ${d.mode === 'SIMULATED' ? 'warning' : 'success'}">${E(d.mode)}</span>
      <span class="text-muted">${d.port ? E(d.port) + ' · ' : ''}last data ${d.last_seen ? ago(d.last_seen, now) : 'never'}
      ${d.patient ? ' · patient ' + E(d.patient) : ' · not assigned to a patient'}</span>
      ${sensors ? `<span class="text-muted">${sensors}</span>` : ''}</li>`;
  }
  function row(r, now, stale, fresh) {
    const isStale = now - new Date(r.measured_at) > stale * 1000;
    return `<tr data-id="${r.id}" class="${fresh ? 'measure-new' : ''} ${isStale ? 'text-muted' : ''}">
      <td>${E(r.device)}</td><td>${E(r.sensor_display)}</td><td>${valueText(r)}</td>
      <td class="small">${fmtTime(r.measured_at)}${isStale ? ' <span class="status-pill" title="Older than ' + stale + ' s">STALE</span>' : ''}</td>
      <td>${srcPill(r)}</td><td>${statusPill(r.status)}</td>
      <td class="small">${r.patient ? E(r.patient) : '<span class="text-muted">—</span>'}</td></tr>`;
  }

  App.iotPanel = function (root) {
    const url = root.dataset.url, body = root.querySelector('tbody'), devs = root.querySelector('[data-devices]');
    const status = root.querySelector('[data-conn]');
    const known = new Set();
    let first = true, live = null;
    async function refresh(evt) {
      if (evt && evt.kind && evt.kind !== 'iot') return;          // other dashboard events
      if (evt && evt.kind === 'iot' && evt.id && known.has(evt.id)) return;  // duplicate event
      const r = await fetch(url, {credentials: 'same-origin', cache: 'no-store'});
      if (!r.ok) return;
      const d = await r.json();
      const now = new Date(d.server_time);
      const fresh = new Set();
      d.readings.forEach(x => { if (!known.has(x.id)) { if (!first) fresh.add(x.id); known.add(x.id); } });
      devs.innerHTML = d.devices.length ? d.devices.map(x => deviceItem(x, now)).join('')
        : '<li class="list-group-item small text-muted">No IoT device has connected yet. Start the serial bridge: python manage.py run_arduino_bridge</li>';
      body.innerHTML = d.readings.length ? d.readings.map(x => row(x, now, d.stale_after_s, fresh.has(x.id))).join('')
        : '<tr><td colspan="7"><div class="empty"><i class="bi bi-inbox" aria-hidden="true"></i>No IoT readings yet</div></td></tr>';
      first = false;
    }
    live = App.live(refresh, {interval: 5000, slowInterval: 10000, onStatus: s => {
      if (status) status.textContent = s === 'live' ? 'live' : 'reconnecting - polling every 5 s';
      if (s === 'live' && live) live.refresh();  // recover everything saved while the socket was down
    }});
  };
  document.querySelectorAll('[data-iot]').forEach(App.iotPanel);
})();
