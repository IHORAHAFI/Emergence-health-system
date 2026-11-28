// Vital Signs panels (operator dashboard + patient portal). All values come from the database:
//   operator: /dashboard/home-vitals.json      patient: /api/vitals/mine/
// The /ws/notify/ WebSocket ("home_vitals" / "measurement" events) only triggers a re-fetch; App.live also polls,
// so nothing is lost while the socket is down. Camera heart rate = CAMERA-ESTIMATED, temperature and blood
// pressure = SIMULATED demonstration data - the badges make sure they are never confused with each other.
(function () {
  const E = s => App.esc(s);
  const fmt = iso => new Date(iso).toLocaleString([], {day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit'});
  const hhmm = iso => new Date(iso).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit'});
  const CAM = '<span class="status-pill info" title="Smartphone camera PPG - an estimate, not a medical-grade measurement">CAMERA-ESTIMATED</span>';
  const SIM = '<span class="status-pill warning" title="Software simulation - demonstration data, NOT a real measurement">SIMULATED</span>';
  const pct = q => q == null ? '—' : `${Math.round(q * 100)}%`;

  App.vs = {
    CAM, SIM, fmt,
    hrText: h => h ? `<b>${Math.round(h.value)}</b> BPM` : '<span class="text-muted">—</span>',
    tempText: t => t ? `<b>${Number(t.value).toFixed(1)}</b> °C` : '<span class="text-muted">—</span>',
    bpText: b => b ? `<b>${b.systolic}/${b.diastolic}</b> mmHg` : '<span class="text-muted">—</span>',
    pct,
    // Three charts from one summary (history arrays are newest first -> reversed for time order).
    charts(root, s, store) {
      const draw = (key, canvasSel, labels, datasets, yTitle) => {
        const el = root.querySelector(canvasSel);
        if (!el || typeof Chart === 'undefined') return;
        const empty = el.parentElement.querySelector('.vs-empty');
        if (!labels.length) { el.hidden = true; if (empty) empty.hidden = false; return; }
        el.hidden = false; if (empty) empty.hidden = true;
        if (store[key]) { store[key].data.labels = labels; store[key].data.datasets.forEach((d, i) => d.data = datasets[i].data); store[key].update('none'); return; }
        store[key] = new Chart(el, {type: 'line', data: {labels, datasets: datasets.map(d => ({
          ...d, borderWidth: 2, pointRadius: 2, tension: .25, fill: false}))},
          options: {responsive: true, maintainAspectRatio: false, animation: false,
            plugins: {legend: {display: datasets.length > 1, position: 'bottom', labels: {boxWidth: 10, font: {size: 11}}}},
            scales: {y: {title: {display: true, text: yTitle}}, x: {title: {display: true, text: 'Time'}, ticks: {maxTicksLimit: 6}}}}});
      };
      const hr = [...s.heart_rate.history].reverse(), t = [...s.temperature.history].reverse(),
        bp = [...s.blood_pressure.history].reverse();
      draw('hr', '[data-chart=hr]', hr.map(x => hhmm(x.measured_at)),
        [{label: 'Camera-estimated BPM', data: hr.map(x => x.value), borderColor: '#0ea5e9'}], 'BPM');
      draw('t', '[data-chart=temp]', t.map(x => hhmm(x.measured_at)),
        [{label: 'Simulated °C', data: t.map(x => x.value), borderColor: '#f59e0b', borderDash: [5, 4]}], '°C');
      draw('bp', '[data-chart=bp]', bp.map(x => hhmm(x.measured_at)),
        [{label: 'Systolic (simulated)', data: bp.map(x => x.systolic), borderColor: '#ef4444', borderDash: [5, 4]},
         {label: 'Diastolic (simulated)', data: bp.map(x => x.diastolic), borderColor: '#6366f1', borderDash: [5, 4]}], 'mmHg');
    },
  };

  // ------------------------------------------------------------ operator panel
  function operatorPanel(root) {
    const url = root.dataset.url, ctl = root.dataset.control;
    const body = root.querySelector('tbody'), status = root.querySelector('[data-conn]');
    const sel = root.querySelector('[name=patient]'), scen = root.querySelector('[name=scenario]');
    const msg = root.querySelector('[data-msg]'), detail = root.querySelector('[data-detail]');
    let chosen = '', live = null, filled = false;
    const store = {};

    function row(p) {
      const h = p.heart_rate, a = p.hr_last_attempt, failed = a && !a.valid && (!h || a.measured_at > h.measured_at);
      const sim = p.simulation && p.simulation.running
        ? `<span class="status-pill success">running · ${E(p.simulation.scenario_label)} · ${p.simulation.interval_s}s</span>` : '<span class="text-muted small">off</span>';
      return `<tr data-pid="${p.patient_id}" class="${String(p.patient_id) === chosen ? 'table-active' : ''}" style="cursor:pointer">
        <td>${E(p.patient)}</td>
        <td>${App.vs.hrText(h)} ${h ? CAM : ''}<div class="small text-muted">${h ? fmt(h.measured_at) + ' · quality ' + pct(h.signal_quality) : 'no successful test'}
          ${failed ? `<br><span class="status-pill danger">last test ${E(a.status)}</span>` : ''}</div></td>
        <td>${App.vs.tempText(p.temperature)} ${p.temperature ? SIM : ''}<div class="small text-muted">${p.temperature ? fmt(p.temperature.measured_at) + ' · ' + E(p.temperature.device || '') : ''}</div></td>
        <td>${App.vs.bpText(p.blood_pressure)} ${p.blood_pressure ? SIM : ''}<div class="small text-muted">${p.blood_pressure ? fmt(p.blood_pressure.measured_at) + ' · ' + E(p.blood_pressure.device || '') : ''}</div></td>
        <td>${sim}</td></tr>`;
    }

    async function refresh(evt) {
      if (evt && evt.kind && !['home_vitals', 'measurement'].includes(evt.kind)) return;
      const r = await fetch(url + (chosen ? `?patient=${chosen}` : ''), {credentials: 'same-origin', cache: 'no-store'});
      if (!r.ok) return;
      const d = await r.json();
      if (!filled) {
        sel.innerHTML = '<option value="">Choose a patient…</option>' + d.control_patients.map(p => `<option value="${p.id}">${E(p.name)}</option>`).join('');
        scen.innerHTML = Object.entries(d.scenarios).map(([k, v]) => `<option value="${k}">${E(v)}</option>`).join('');
        filled = true;
      }
      body.innerHTML = d.patients.length ? d.patients.map(row).join('')
        : '<tr><td colspan="5"><div class="empty"><i class="bi bi-inbox" aria-hidden="true"></i>No vital signs yet. Start the simulator for a patient, or ask a patient to run the Heart Rate Test in the app.</div></td></tr>';
      if (d.selected) {
        detail.hidden = false;
        detail.querySelector('[data-who]').textContent = d.selected.patient;
        App.vs.charts(detail, d.selected, store);
      }
    }
    body.addEventListener('click', e => {
      const tr = e.target.closest('tr[data-pid]');
      if (!tr) return;
      chosen = tr.dataset.pid; sel.value = chosen;
      Object.values(store).forEach(c => c.destroy()); for (const k in store) delete store[k];
      live && live.refresh();
    });
    root.querySelectorAll('[data-action]').forEach(btn => btn.addEventListener('click', async () => {
      if (!sel.value) { msg.textContent = 'Choose a patient first.'; return; }
      btn.disabled = true;
      const f = new FormData(root.querySelector('form'));
      f.set('action', btn.dataset.action);
      const r = await fetch(ctl, {method: 'POST', body: f, credentials: 'same-origin', headers: {'X-CSRFToken': App.csrf()}});
      const d = await r.json().catch(() => ({}));
      msg.textContent = r.ok ? (btn.dataset.action === 'start' ? 'Simulation started - readings are labelled SIMULATED.' : 'Simulation stopped.') : (d.detail || 'Failed.');
      btn.disabled = false;
      chosen = sel.value;
      live && live.refresh();
    }));
    live = App.live(refresh, {interval: 5000, slowInterval: 10000, onStatus: s => {
      if (status) status.textContent = s === 'live' ? 'live' : 'reconnecting - polling every 5 s';
      if (s === 'live' && live) live.refresh();
    }});
  }

  // ------------------------------------------------------------ patient portal
  function patientPanel(root) {
    const url = root.dataset.url, store = {};
    const set = (k, html) => { const el = root.querySelector(`[data-v=${k}]`); if (el) el.innerHTML = html; };
    async function refresh(evt) {
      if (evt && evt.kind && !['home_vitals', 'measurement'].includes(evt.kind)) return;
      const r = await fetch(url, {credentials: 'same-origin', cache: 'no-store'});
      if (!r.ok) return;
      const s = await r.json();
      const h = s.heart_rate.latest, a = s.heart_rate.last_attempt, t = s.temperature.latest, b = s.blood_pressure.latest;
      set('hr', App.vs.hrText(h));
      set('hr-meta', h ? `${fmt(h.measured_at)} · ${E(h.status)} · signal quality ${pct(h.signal_quality)}` : 'No successful test yet - use the mobile app: Vital signs → Heart Rate Test.');
      set('hr-warn', a && !a.valid && (!h || a.measured_at > h.measured_at) ? `<span class="status-pill danger">Last test ${E(a.status)}: ${E(a.failure_reason)}</span>` : '');
      set('temp', App.vs.tempText(t));
      set('temp-meta', t ? `${fmt(t.measured_at)} · device ${E(t.device || '')}` : 'No simulated readings yet.');
      set('bp', App.vs.bpText(b));
      set('bp-meta', b ? `${fmt(b.measured_at)} · device ${E(b.device || '')}` : 'No simulated readings yet.');
      App.vs.charts(root, s, store);
    }
    App.live(refresh, {interval: 10000, slowInterval: 20000});
  }

  document.querySelectorAll('[data-vitals-operator]').forEach(operatorPanel);
  document.querySelectorAll('[data-vitals-patient]').forEach(patientPanel);
})();
