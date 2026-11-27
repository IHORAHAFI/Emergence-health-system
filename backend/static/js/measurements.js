// Live "Patient measurements" panel (phone-camera heart rate + Bluetooth thermometer).
// Data always comes from the permission-checked JSON feed (the database); the /ws/notify/ WebSocket only says
// "a measurement was saved" and App.live falls back to polling when the socket is down, so a dropped connection
// never loses a measurement - the next fetch after reconnecting returns everything stored.
// Rows are keyed by measurement id: an event received twice never shows a row twice.
(function () {
  const E = s => App.esc ? App.esc(s) : String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const STATUS = {
    estimated: ['primary', 'ESTIMATED', 'Heart rate estimated from the phone camera (PPG) - not a medical-grade measurement'],
    measured: ['success', 'MEASURED', 'Temperature received from a Bluetooth thermometer'],
    failed: ['danger', 'FAILED', 'The measurement did not complete - no value'],
    inconclusive: ['warning', 'INCONCLUSIVE', 'Data was received but could not be validated - no value'],
  };
  const fmtTime = iso => new Date(iso).toLocaleString([], {day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit'});
  const valueText = m => m.valid && m.value != null
    ? `<b>${m.kind === 'temperature' ? Number(m.value).toFixed(1) : Math.round(m.value)}</b> ${E(m.unit)}`
    : `<span class="text-muted" title="${E(m.failure_reason)}">— no valid value</span>`;
  const deviceText = d => {
    if (!d || typeof d !== 'object') return '';
    return [d.name, d.model, d.manufacturer, d.platform].filter(Boolean).map(E).join(' · ') || '';
  };
  function pill(m) {
    const [cls, label, title] = STATUS[m.status] || ['secondary', E(m.status), ''];
    return `<span class="status-pill ${cls}" title="${title}">${label}</span>`;
  }
  function row(m, fresh) {
    const reason = m.valid ? '' : `<span class="d-block small text-muted">${E(m.failure_reason)}</span>`;
    const em = m.emergency ? ` · <a href="/dashboard/emergency/${m.emergency_id}/">${E(m.emergency)}</a>` : '';
    return `<tr data-id="${m.id}" class="${fresh ? 'measure-new' : ''}">
      <td><span class="fw-semibold">${E(m.patient)}</span><span class="d-block small text-muted">${em.replace(/^ · /, '') || 'no active emergency'}</span></td>
      <td>${E(m.kind_display)}</td>
      <td>${valueText(m)}${reason}</td>
      <td class="small">${fmtTime(m.measured_at)}</td>
      <td class="small">${E(m.source_display)}<span class="d-block text-muted">${E(m.method_display)}</span></td>
      <td>${pill(m)}</td>
      <td class="small text-muted">${deviceText(m.device)}</td></tr>`;
  }
  function latestCard(p) {
    const v = (m, label) => m
      ? `<span class="me-3">${label} ${valueText(m)} <span class="small text-muted">${m.status === 'estimated' ? 'est.' : ''} ${fmtTime(m.measured_at)}</span></span>`
      : `<span class="me-3 text-muted">${label} —</span>`;
    const last = p.last_attempt && !p.last_attempt.valid
      ? `<span class="small text-danger">last attempt ${STATUS[p.last_attempt.status][1]}</span>` : '';
    return `<li class="list-group-item small"><span class="fw-semibold me-2">${E(p.patient)}</span>
      ${v(p.heart_rate, '<i class="bi bi-heart-pulse text-danger" aria-label="Heart rate"></i>')}
      ${v(p.temperature, '<i class="bi bi-thermometer-half text-warning" aria-label="Temperature"></i>')}${last}</li>`;
  }
  function toast(m) {
    let box = document.getElementById('measure-toasts');
    if (!box) {
      box = Object.assign(document.createElement('div'), {id: 'measure-toasts', className: 'measure-toasts'});
      box.setAttribute('role', 'status'); box.setAttribute('aria-live', 'polite');
      document.body.appendChild(box);
    }
    const t = document.createElement('div');
    t.className = `measure-toast ${m.valid ? '' : 'bad'}`;
    t.innerHTML = `<i class="bi ${m.kind === 'temperature' ? 'bi-thermometer-half' : 'bi-heart-pulse'}" aria-hidden="true"></i>
      <span><b>New ${E(m.kind_display.toLowerCase())}</b> · ${E(m.patient)}<br>${valueText(m)} ${pill(m)}</span>`;
    box.appendChild(t);
    setTimeout(() => t.remove(), 9000);
  }

  App.measurementsPanel = function (root) {
    const url = root.dataset.url, body = root.querySelector('tbody'), latest = root.querySelector('[data-latest]');
    const status = root.querySelector('[data-conn]');
    const known = new Map();
    let first = true;
    async function refresh(evt) {
      // other WebSocket events (locations, journeys...) also arrive here; only fetch for measurements or polls
      if (evt && evt.kind && evt.kind !== 'measurement') return;
      if (evt && evt.kind === 'measurement' && known.has(evt.id)) return;  // duplicate event
      const r = await fetch(url, {credentials: 'same-origin', cache: 'no-store'});
      if (!r.ok) return;
      const d = await r.json();
      const fresh = [];
      for (const m of d.measurements) {
        const prev = known.get(m.id);
        if (!prev) { if (!first) fresh.push(m); }
        known.set(m.id, m);
      }
      const rows = [...known.values()].sort((a, b) => (b.measured_at.localeCompare(a.measured_at)) || b.id - a.id).slice(0, 50);
      const freshIds = new Set(fresh.map(m => m.id));
      body.innerHTML = rows.length ? rows.map(m => row(m, freshIds.has(m.id))).join('')
        : '<tr><td colspan="7"><div class="empty"><i class="bi bi-inbox" aria-hidden="true"></i>No measurements yet</div></td></tr>';
      if (latest) latest.innerHTML = d.latest.map(latestCard).join('');
      fresh.slice(0, 3).forEach(toast);
      first = false;
    }
    App.live(refresh, {interval: 5000, slowInterval: 20000,
      onStatus: s => { if (status) status.textContent = s === 'live' ? 'live' : 'reconnecting - polling every 5 s'; }});
  };
  document.querySelectorAll('[data-measurements]').forEach(App.measurementsPanel);
})();
