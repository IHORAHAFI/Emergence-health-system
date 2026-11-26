// Live location helpers shared by the patient tracking page and the operator maps (Leaflet + OpenStreetMap).
//
// App.locationBadge(status)  -> HTML badge: LIVE LOCATION / LAST KNOWN LOCATION / MANUAL / UNAVAILABLE (+ age)
// App.shareLocation(opts)    -> the patient's browser shares its GPS for ONE open emergency:
//   * uses navigator.geolocation.watchPosition (asks permission; the reason is shown on the page first)
//   * sends a position only after moving >= 15 m or every 60 s at most (battery + no flood of requests)
//   * real device GPS only - nothing is guessed from IP addresses or filled with default coordinates
//   * every failure is reported in plain words (permission denied, no GPS signal, timeout, offline, rejected)
//   * returns {stop()}; sharing also stops when the page closes. Browsers cannot share in the background.
(function () {
  const E = s => App.esc(s);

  function age(seconds) {
    if (seconds == null) return '';
    if (seconds < 60) return `${seconds} s ago`;
    if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
    return `${Math.round(seconds / 3600)} h ago`;
  }
  App.locationAge = age;

  App.locationBadge = function (s) {
    if (!s) return '';
    const cls = {live: 'bg-success', last_known: 'bg-warning text-dark', manual: 'bg-primary', simulated: 'badge-sim',
                 unavailable: 'bg-danger'}[s.state] || 'bg-secondary';
    const bits = [];
    if (s.age_seconds != null && s.state !== 'unavailable') bits.push(age(s.age_seconds));
    if (s.accuracy_m != null) bits.push(`±${Math.round(s.accuracy_m)} m`);
    return `<span class="badge ${cls}">${E(s.label)}</span>` + (bits.length ? ` <span class="small text-muted">${E(bits.join(' · '))}</span>` : '')
      + (s.note ? `<div class="small ${s.state === 'unavailable' ? 'text-danger' : 'text-warning'}">${E(s.note)}</div>` : '');
  };

  // marker colour by location state (live = solid red, last known = amber, manual = blue)
  App.locationColor = s => ({live: '#d92d20', last_known: '#f79009', manual: '#2e90fa', simulated: '#7a5af8'}[s && s.state] || '#98a2b3');

  App.shareLocation = function ({url, onStatus, onPosition}) {
    const MIN_MOVE = 15, MAX_GAP = 60000;
    let last = null, lastSent = 0, watch = null, stopped = false, inFlight = false;
    const report = (kind, text) => onStatus && onStatus(kind, text);

    function dist(a, b) {  // metres
      const R = 6371000, r = Math.PI / 180, dLat = (b.lat - a.lat) * r, dLng = (b.lng - a.lng) * r;
      const h = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * r) * Math.cos(b.lat * r) * Math.sin(dLng / 2) ** 2;
      return 2 * R * Math.asin(Math.sqrt(h));
    }

    async function send(p) {
      if (inFlight) return;
      inFlight = true;
      const body = {latitude: p.coords.latitude, longitude: p.coords.longitude, accuracy: p.coords.accuracy,
                    timestamp: new Date(p.timestamp).toISOString(), source: 'gps'};
      try {
        const r = await fetch(url, {method: 'POST', credentials: 'same-origin',
          headers: {'Content-Type': 'application/json', 'X-CSRFToken': App.csrf()}, body: JSON.stringify(body)});
        const d = await r.json().catch(() => ({}));
        if (!r.ok) {
          report('error', `Location not accepted: ${d.error || d.detail || r.status}`);
          if (r.status === 400 && /closed/.test(d.error || '')) stop(true);
          return;
        }
        last = {lat: body.latitude, lng: body.longitude}; lastSent = Date.now();
        report('live', `Sharing live location (±${Math.round(body.accuracy)} m, sent ${new Date().toLocaleTimeString()})`);
        onPosition && onPosition(d.location);
      } catch (e) {
        report('offline', 'No internet connection - your location will be sent again when the connection returns.');
      } finally { inFlight = false; }
    }

    function stop(quiet) {  // quiet: keep the explanation already shown (e.g. permission denied)
      stopped = true;
      if (watch != null) navigator.geolocation.clearWatch(watch);
      watch = null;
      if (!quiet) report('stopped', 'Location sharing stopped.');
    }

    if (!('geolocation' in navigator)) { report('error', 'This browser cannot share GPS location. Tell the operator where you are by phone.'); return {stop() {}}; }
    if (!window.isSecureContext) { report('error', 'Location needs a secure (https) connection.'); return {stop() {}}; }
    report('waiting', 'Finding your location… if the browser asks, choose Allow.');
    watch = navigator.geolocation.watchPosition(p => {
      if (stopped) return;
      const here = {lat: p.coords.latitude, lng: p.coords.longitude};
      if (!last || dist(last, here) >= MIN_MOVE || Date.now() - lastSent >= MAX_GAP) send(p);
    }, err => {
      const why = {1: 'Location permission was denied. Allow location for this site in your browser settings, or tell the operator where you are by phone.',
                   2: 'Your device cannot find its GPS position right now (no signal). Trying again…',
                   3: 'Finding your position is taking long. Trying again…'}[err.code] || 'Location error.';
      report(err.code === 1 ? 'denied' : 'error', why);
      if (err.code === 1) stop(true);
    }, {enableHighAccuracy: true, maximumAge: 15000, timeout: 30000});
    addEventListener('pagehide', () => { if (watch != null) navigator.geolocation.clearWatch(watch); });
    return {stop: () => stop(false)};
  };
})();
