/// Decoders for the standard Bluetooth SIG health profiles (same profiles as the web app's ble-health.js).
///
///   Health Thermometer 0x1809 / Temperature Measurement 0x2A1C
///   Blood Pressure     0x1810 / Blood Pressure Measurement 0x2A35
///   Heart Rate         0x180D / Heart Rate Measurement 0x2A37
///   Pulse Oximeter     0x1822 / PLX Spot-check 0x2A5E, PLX Continuous 0x2A5F
///
/// Each decoder returns backend field names (temperature_c, heart_rate_bpm, ...) or null when the
/// packet holds no usable value. Nothing here ever invents a value.
library;

/// IEEE-11073 16-bit SFLOAT. Returns null for NaN / NRes / ±INF / reserved.
double? sfloat(int lo, int hi) {
  final raw = lo | (hi << 8);
  if (const {0x07FF, 0x0800, 0x07FE, 0x0802, 0x0801}.contains(raw)) return null;
  var mantissa = raw & 0x0FFF;
  if (mantissa >= 0x0800) mantissa -= 0x1000;
  var exponent = raw >> 12;
  if (exponent >= 0x8) exponent -= 0x10;
  return mantissa * _pow10(exponent);
}

/// IEEE-11073 32-bit FLOAT.
double? float32(List<int> b, int o) {
  final mantissaRaw = b[o] | (b[o + 1] << 8) | (b[o + 2] << 16);
  if (const {0x7FFFFF, 0x800000, 0x7FFFFE, 0x800002, 0x800001}.contains(mantissaRaw)) return null;
  var mantissa = mantissaRaw;
  if (mantissa >= 0x800000) mantissa -= 0x1000000;
  var exponent = b[o + 3];
  if (exponent >= 0x80) exponent -= 0x100;
  return mantissa * _pow10(exponent);
}

double _pow10(int e) {
  var r = 1.0;
  for (var i = 0; i < e.abs(); i++) {
    r *= 10;
  }
  return e < 0 ? 1 / r : r;
}

Map<String, num>? parseTemperature(List<int> b) {
  if (b.length < 5) return null;
  var t = float32(b, 1);
  if (t == null) return null;
  if (b[0] & 0x01 != 0) t = (t - 32) * 5 / 9; // Fahrenheit -> Celsius
  return {'temperature_c': double.parse(t.toStringAsFixed(1))};
}

Map<String, num>? parseBloodPressure(List<int> b) {
  if (b.length < 7) return null;
  final flags = b[0];
  var sys = sfloat(b[1], b[2]), dia = sfloat(b[3], b[4]);
  if (sys == null || dia == null) return null;
  if (flags & 0x01 != 0) {
    sys *= 7.50062; // kPa -> mmHg
    dia *= 7.50062;
  }
  final out = <String, num>{'systolic_mmhg': sys.round(), 'diastolic_mmhg': dia.round()};
  var o = 7;
  if (flags & 0x02 != 0) o += 7; // timestamp
  if (flags & 0x04 != 0 && b.length >= o + 2) {
    final pr = sfloat(b[o], b[o + 1]);
    if (pr != null) out['heart_rate_bpm'] = pr.round();
  }
  return out;
}

Map<String, num>? parseHeartRate(List<int> b) {
  if (b.length < 2) return null;
  final wide = b[0] & 0x01 != 0;
  if (wide && b.length < 3) return null;
  final hr = wide ? b[1] | (b[2] << 8) : b[1];
  return hr == 0 ? null : {'heart_rate_bpm': hr}; // 0 = sensor not in contact
}

/// PLX spot-check (0x2A5E) and continuous (0x2A5F) both start with flags, SpO2, pulse rate.
Map<String, num>? parsePlx(List<int> b) {
  if (b.length < 5) return null;
  final spo2 = sfloat(b[1], b[2]), pr = sfloat(b[3], b[4]);
  final out = <String, num>{};
  if (spo2 != null) out['spo2_pct'] = spo2.round();
  if (pr != null) out['heart_rate_bpm'] = pr.round();
  return out.isEmpty ? null : out;
}
