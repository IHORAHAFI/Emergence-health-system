// Shared helpers for maps and CSRF-protected fetch calls.
window.App = {
  dark: document.documentElement.dataset.bsTheme === 'dark',
  tiles(map) {
    // OpenStreetMap standard tiles (free, no API key; tile usage policy: https://operations.osmfoundation.org/policies/tiles/).
    // * referrerPolicy: OSM requires a Referer header; Django's same-origin Referrer-Policy strips it from cross-origin
    //   requests (that gave HTTP 403 "Access blocked"), so the tile requests send the site origin explicitly.
    // * CARTO basemaps were used before, but CARTO now returns "API KEY REQUIRED" placeholder tiles without a key.
    // * Dark theme: OSM has no dark style, so the tiles are darkened with a CSS filter (class map-dark).
    const layer = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19, referrerPolicy: 'strict-origin-when-cross-origin',
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    }).addTo(map);
    if (App.dark) map.getContainer().classList.add('map-dark');
    // a map that cannot load its tiles must say so instead of showing an empty grey box
    let failed = 0, warned = false;
    layer.on('tileerror', () => {
      if (++failed < 4 || warned) return;
      warned = true;
      const note = L.control({position: 'bottomleft'});
      note.onAdd = () => Object.assign(L.DomUtil.create('div', 'leaflet-bar'), {
        style: 'background:#fff;color:#b42318;padding:4px 8px;font-size:12px;max-width:240px',
        textContent: 'Map tiles could not be loaded (offline or blocked). Markers and coordinates are still correct.'});
      note.addTo(map);
    });
    return map;
  },
  icon(cls, color) {
    return L.divIcon({className: '', iconSize: [28, 28],
      html: `<i class="bi ${cls}" style="font-size:26px;color:${color}"></i>`});
  },
  csrf() {
    const m = document.cookie.match(/csrftoken=([^;]+)/);
    return m ? m[1] : '';
  },
  // Chart.js helper with the site palette. Shows a friendly message when there is no data.
  palette: ['#14b8a6', '#6ea8ff', '#f59e0b', '#ef4444', '#a78bfa', '#22c55e', '#0ea5e9', '#f472b6', '#94a3b8'],
  chart(id, type, labels, data, opts = {}) {
    const el = document.getElementById(id);
    if (!el || typeof Chart === 'undefined') return null;
    if (!data.length || data.every(v => !v)) {
      el.replaceWith(Object.assign(document.createElement('p'), {className: 'text-muted small text-center py-5 mb-0', textContent: 'No data yet.'}));
      return null;
    }
    const colors = opts.colors || (type === 'line' ? ['#1d6fd8'] : App.palette);
    return new Chart(el, {
      type, data: {labels, datasets: [{label: opts.label || 'Emergencies', data,
        backgroundColor: type === 'line' ? 'rgba(29,111,216,.12)' : colors, borderColor: type === 'line' ? '#1d6fd8' : (type === 'doughnut' ? '#fff' : colors),
        borderWidth: type === 'line' ? 2 : 1, fill: type === 'line', tension: .3, borderRadius: type === 'bar' ? 6 : 0}]},
      options: {responsive: true, maintainAspectRatio: false,
        plugins: {legend: {display: type === 'doughnut', position: 'bottom', labels: {boxWidth: 12, font: {size: 11}}}},
        scales: type === 'doughnut' ? {} : {y: {beginAtZero: true, ticks: {precision: 0}}, x: {grid: {display: false}}}}
    });
  },
  esc(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  },
};
// Prevent double submission of any form marked data-once.
document.addEventListener('submit', e => {
  const f = e.target;
  if (f.dataset.once !== undefined) {
    if (f.dataset.sent) { e.preventDefault(); return; }
    f.dataset.sent = '1';
    f.querySelectorAll('button[type=submit],button:not([type])').forEach(b => { b.disabled = true; b.innerText = 'Sending…'; });
  }
});

// Accessible confirmation for destructive actions: <form data-confirm="..."> or <button data-confirm="...">.
(function () {
  let pending = null;
  document.addEventListener('submit', e => {
    const f = e.target, btn = e.submitter;
    const q = (btn && btn.dataset.confirm) || f.dataset.confirm;
    if (!q || f.dataset.confirmed) return;
    e.preventDefault(); e.stopImmediatePropagation();
    pending = {form: f, btn};
    const m = document.getElementById('confirm-modal');
    if (!m || !window.bootstrap) { if (window.confirm(q)) { f.dataset.confirmed = '1'; f.requestSubmit(btn); } return; }
    document.getElementById('confirm-text').textContent = q;
    bootstrap.Modal.getOrCreateInstance(m).show();
  }, true);
  document.addEventListener('click', e => {
    if (e.target.id !== 'confirm-yes' || !pending) return;
    const {form, btn} = pending; pending = null;
    bootstrap.Modal.getInstance(document.getElementById('confirm-modal')).hide();
    form.dataset.confirmed = '1';
    form.requestSubmit(btn || undefined);
  });
})();
// Leaflet markers: the same information is always listed on the page, so markers are not separate tab stops.
if (window.L && L.Marker) L.Marker.mergeOptions({keyboard: false});
// Table rows that open a page on click are also reachable and openable with the keyboard (Tab + Enter).
document.querySelectorAll('tr.clickable[onclick]').forEach(tr => {
  tr.tabIndex = 0;
  tr.setAttribute('role', 'link');
  const first = tr.querySelector('td');
  if (first && !tr.getAttribute('aria-label')) tr.setAttribute('aria-label', 'Open ' + first.textContent.trim().replace(/\s+/g, ' '));
  tr.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); tr.click(); } });
});

// Chart.js look for both themes
if (window.Chart) {
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  Chart.defaults.color = App.dark ? '#9fb0c8' : '#4b5a6c';
  Chart.defaults.borderColor = App.dark ? 'rgba(159,176,200,.12)' : 'rgba(15,35,65,.08)';
}
// Sparkline: tiny trend line with no axes. Real values only; draws nothing when there is no data.
App.spark = function (canvas, values, color) {
  if (!canvas || !window.Chart) return null;
  const data = (values || []).filter(v => v != null);
  if (data.length < 2) { canvas.replaceWith(Object.assign(document.createElement('span'), {className: 'kpi-note', textContent: data.length ? '' : '-'})); return null; }
  const c = color || '#14b8a6';
  return new Chart(canvas, {type: 'line', data: {labels: data.map((_, i) => i), datasets: [{data, borderColor: c, borderWidth: 2, pointRadius: 0, tension: .35, fill: true,
    backgroundColor: c + '22'}]}, options: {responsive: true, maintainAspectRatio: false, animation: false, events: [], plugins: {legend: {display: false}, tooltip: {enabled: false}},
    scales: {x: {display: false}, y: {display: false}}}});
};
document.addEventListener('DOMContentLoaded', () => document.querySelectorAll('canvas[data-spark]').forEach(cv => {
  try { App.spark(cv, JSON.parse(cv.dataset.spark), cv.dataset.color); } catch (e) { /* bad data: leave empty */ }
}));

// Refresh the parts of a page marked data-refresh (matched by id) IN PLACE instead of location.reload().
// Why: browsers grant permission to play sound per page load. A full reload threw away the operator's
// "Enable emergency sound" permission and cut off a siren that was playing - the siren then looked "broken".
App.refresh = async function () {
  const regions = document.querySelectorAll('[data-refresh]');
  if (!regions.length) return false;
  let r;
  try { r = await fetch(location.href, {credentials: 'same-origin', cache: 'no-store'}); } catch (e) { return false; }
  if (!r.ok || r.redirected) return false;  // e.g. logged out: leave the page as it is
  const doc = new DOMParser().parseFromString(await r.text(), 'text/html');
  const active = document.activeElement;
  regions.forEach(el => {
    if (active && el.contains(active) && active.closest('form')) return;  // never wipe a form being filled in
    const fresh = doc.getElementById(el.id);
    if (fresh) el.innerHTML = fresh.innerHTML;
    el.querySelectorAll('canvas[data-spark]').forEach(cv => {
      try { App.spark(cv, JSON.parse(cv.dataset.spark), cv.dataset.color); } catch (e) { /* leave empty */ }
    });
  });
  document.dispatchEvent(new CustomEvent('app:refreshed'));
  return true;
};
