// Web Bluetooth readers for STANDARD Bluetooth SIG health profiles.
//
// Requirements (checked by BleHealth.check()):
//   * Chrome or Edge on Windows 10+, Android, macOS or ChromeOS (not Firefox / Safari / iOS).
//   * A secure page: https://... or http://localhost / http://127.0.0.1 (NOT http://192.168.x.x).
//   * Bluetooth switched on, and a device that implements the standard GATT profile:
//       Health Thermometer 0x1809 / Temperature Measurement 0x2A1C (indicate)
//       Blood Pressure     0x1810 / Blood Pressure Measurement 0x2A35 (indicate)
//       Heart Rate         0x180D / Heart Rate Measurement 0x2A37 (notify)
//       Pulse Oximeter     0x1822 / PLX Spot-check 0x2A5E (indicate), PLX Continuous 0x2A5F (notify)
//   Devices with proprietary protocols (many cheap oximeters, most ECGs) cannot be read here:
//   use the vendor app + manual entry, or the Python BLE gateway (tools/ble_gateway.py).
(function () {
  // IEEE-11073 16-bit SFLOAT: 4-bit exponent, 12-bit mantissa (both signed)
  function sfloat(dv, o) {
    const raw = dv.getUint16(o, true);
    let m = raw & 0x0fff, e = raw >> 12;
    if ([0x07ff, 0x0800, 0x07fe, 0x0802, 0x0801].includes(m)) return null; // NaN / NRes / +-INF / reserved
    if (m >= 0x0800) m -= 0x1000;
    if (e >= 0x8) e -= 0x10;
    return m * Math.pow(10, e);
  }
  // IEEE-11073 32-bit FLOAT: 8-bit exponent, 24-bit mantissa
  function float32(dv, o) {
    const raw = dv.getUint32(o, true);
    let m = raw & 0xffffff, e = raw >> 24;
    if (m === 0x7fffff || m === 0x800000 || m === 0x7ffffe || m === 0x800002 || m === 0x800001) return null;
    if (m >= 0x800000) m -= 0x1000000;
    if (e >= 0x80) e -= 0x100;
    return m * Math.pow(10, e);
  }
  const round1 = v => v == null ? null : Math.round(v * 10) / 10;

  const PROFILES = {
    thermometer: {
      label: 'Thermometer', service: 'health_thermometer', characteristic: 'temperature_measurement',
      parse(dv) {
        const flags = dv.getUint8(0);
        let t = float32(dv, 1);
        if (t != null && (flags & 1)) t = (t - 32) * 5 / 9; // Fahrenheit -> Celsius
        return {temperature_c: round1(t)};
      }
    },
    blood_pressure: {
      label: 'Blood pressure monitor', service: 'blood_pressure', characteristic: 'blood_pressure_measurement',
      parse(dv) {
        const flags = dv.getUint8(0), kpa = flags & 1;
        const conv = v => v == null ? null : Math.round(kpa ? v * 7.50062 : v);
        let o = 7; // flags(1) + systolic, diastolic, mean arterial pressure (3 x SFLOAT)
        if (flags & 2) o += 7; // timestamp present
        const out = {systolic_mmhg: conv(sfloat(dv, 1)), diastolic_mmhg: conv(sfloat(dv, 3))};
        if (flags & 4) { const pr = sfloat(dv, o); out.heart_rate_bpm = pr == null ? null : Math.round(pr); }
        return out;
      }
    },
    heart_rate: {
      label: 'Heart-rate sensor', service: 'heart_rate', characteristic: 'heart_rate_measurement',
      parse(dv) {
        const flags = dv.getUint8(0);
        return {heart_rate_bpm: (flags & 1) ? dv.getUint16(1, true) : dv.getUint8(1)};
      }
    },
    pulse_oximeter: {
      label: 'Pulse oximeter', service: 0x1822, characteristic: [0x2a5e, 0x2a5f], // PLX spot-check / continuous
      parse(dv) {
        const spo2 = sfloat(dv, 1), pr = sfloat(dv, 3);
        return {spo2_pct: spo2 == null ? null : Math.round(spo2), heart_rate_bpm: pr == null ? null : Math.round(pr)};
      }
    },
  };
  const ALL_SERVICES = Object.values(PROFILES).map(p => p.service);
  const SERVICE_NAMES = {'00001809': 'Health Thermometer', '00001810': 'Blood Pressure', '0000180d': 'Heart Rate',
    '00001822': 'Pulse Oximeter', '0000180f': 'Battery', '0000180a': 'Device Information'};
  const serviceName = uuid => SERVICE_NAMES[uuid.slice(0, 8)] || uuid;

  /** Explains exactly why Web Bluetooth cannot be used on this page, or returns {ok: true}. */
  async function check() {
    if (!window.isSecureContext) return {ok: false, reason: 'insecure',
      message: `Bluetooth is blocked on this address (${location.host}). Open the page via http://127.0.0.1:8000 or http://localhost:8000 on this computer, or via HTTPS. On a phone connected by USB use "adb reverse tcp:8000 tcp:8000".`};
    if (!navigator.bluetooth) return {ok: false, reason: 'unsupported',
      message: 'This browser does not support Web Bluetooth. Use Google Chrome or Microsoft Edge (Firefox, Safari and all iPhone browsers are not supported).'};
    if (navigator.bluetooth.getAvailability) {
      try {
        // Some systems never answer this query: give up after 3 s and let the user try anyway.
        const avail = await Promise.race([navigator.bluetooth.getAvailability(), new Promise(r => setTimeout(() => r('unknown'), 3000))]);
        if (avail === 'unknown') return {ok: true, uncertain: true};
        if (!avail)
          return {ok: false, reason: 'off', message: 'No Bluetooth adapter is available, or Bluetooth is switched off. Turn it on in Windows Settings > Bluetooth & devices.'};
      } catch (e) { /* not fatal */ }
    }
    return {ok: true};
  }

  /**
   * Connects to a device of the given kind and subscribes to its measurements.
   * opts.acceptAll  - show every nearby BLE device (for devices that do not advertise the service UUID)
   * opts.onReading(values, device), opts.onStatus(state, device, detail)
   * Returns a handle {device, disconnect()}. Reconnects automatically (3 attempts) if the link drops.
   */
  async function connect(kind, opts) {
    const p = PROFILES[kind];
    if (!p) throw new Error('Unknown device type ' + kind);
    const c = await check();
    if (!c.ok) throw Object.assign(new Error(c.message), {name: 'BluetoothUnavailable'});
    const onStatus = opts.onStatus || (() => {}), onReading = opts.onReading;
    const device = await navigator.bluetooth.requestDevice(opts.acceptAll
      ? {acceptAllDevices: true, optionalServices: [...ALL_SERVICES, 'battery_service']}
      : {filters: [{services: [p.service]}], optionalServices: ['battery_service']});
    let manualClose = false, attempts = 0;

    async function subscribe() {
      onStatus('connecting', device);
      const server = await device.gatt.connect();
      let service;
      try {
        service = await server.getPrimaryService(p.service);
      } catch (e) {
        // Tell the user what the device DOES offer, so a wrong / proprietary device is obvious.
        let found = [];
        try { found = (await server.getPrimaryServices()).map(s => serviceName(s.uuid)); } catch (e2) { /* ignore */ }
        server.disconnect();
        throw Object.assign(new Error(`"${device.name || 'This device'}" does not offer the standard ${p.label} service. ` +
          (found.length ? `Services visible: ${found.join(', ')}. ` : '') +
          'It probably uses a proprietary protocol - use its own app and enter the values manually, or the Python BLE gateway.'), {name: 'NotHealthDevice'});
      }
      const chars = Array.isArray(p.characteristic) ? p.characteristic : [p.characteristic];
      let subscribed = 0;
      for (const uuid of chars) {
        try {
          const ch = await service.getCharacteristic(uuid);
          ch.addEventListener('characteristicvaluechanged', e => {
            try { onReading(p.parse(e.target.value), device); }
            catch (err) { onStatus('bad-data', device, err.message); }
          });
          await ch.startNotifications(); // also enables INDICATE for thermometer / BP / PLX spot-check
          subscribed++;
        } catch (e) { /* this optional characteristic is not offered */ }
      }
      if (!subscribed) { server.disconnect(); throw new Error('Connected, but the device does not provide a measurement characteristic.'); }
      let battery = null;
      try {
        const b = await (await server.getPrimaryService('battery_service')).getCharacteristic('battery_level');
        battery = (await b.readValue()).getUint8(0);
      } catch (e) { /* battery service is optional */ }
      attempts = 0;
      onStatus('connected', device, {battery});
    }

    device.addEventListener('gattserverdisconnected', async () => {
      if (manualClose) { onStatus('closed', device); return; }
      while (attempts < 3) {
        attempts++;
        const wait = 1000 * Math.pow(2, attempts - 1);
        onStatus('reconnecting', device, {attempt: attempts, wait});
        await new Promise(r => setTimeout(r, wait));
        try { await subscribe(); return; } catch (e) { /* try again */ }
      }
      onStatus('disconnected', device);
    });

    await subscribe();
    return {device, disconnect() { manualClose = true; if (device.gatt.connected) device.gatt.disconnect(); else onStatus('closed', device); }};
  }

  window.BleHealth = {connect, check, PROFILES, _sfloat: sfloat, _float32: float32};
})();
