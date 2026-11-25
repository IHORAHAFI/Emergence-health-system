/// One stored measurement (mirrors VitalSignReading.as_dict).
class VitalReading {
  final int id;
  final DateTime measuredAt;
  final String source; // bluetooth | manual | simulated
  final bool simulated;
  final String? device, emergency, testRequest, risk;
  final double? temperature;
  final int? heartRate, spo2, systolic, diastolic, respiratoryRate, news2;

  VitalReading.fromJson(Map<String, dynamic> j)
      : id = j['id'],
        measuredAt = DateTime.parse(j['measured_at']).toLocal(),
        source = j['source'] ?? '',
        simulated = j['simulated'] == true,
        device = j['device'],
        emergency = j['emergency'],
        testRequest = j['test_request'],
        risk = (j['risk'] ?? '') == '' ? null : j['risk'],
        temperature = (j['temperature_c'] as num?)?.toDouble(),
        heartRate = j['heart_rate_bpm'],
        spo2 = j['spo2_pct'],
        systolic = j['systolic_mmhg'],
        diastolic = j['diastolic_mmhg'],
        respiratoryRate = j['respiratory_rate'],
        news2 = j['news2_score'];
}

/// Most recent value of each vital across a list of readings (newest first), keeping its origin.
class LatestVital {
  final String value;
  final bool simulated;
  final DateTime at;
  LatestVital(this.value, this.simulated, this.at);
}

Map<String, LatestVital> latestByVital(List<VitalReading> rs) {
  final out = <String, LatestVital>{};
  void put(String k, String? v, VitalReading r) {
    if (v != null && !out.containsKey(k)) out[k] = LatestVital(v, r.simulated, r.measuredAt);
  }

  for (final r in rs) {
    put('temperature', r.temperature?.toStringAsFixed(1), r);
    put('heart_rate', r.heartRate?.toString(), r);
    put('spo2', r.spo2?.toString(), r);
    put('blood_pressure', r.systolic != null ? '${r.systolic}/${r.diastolic}' : null, r);
    put('respiratory', r.respiratoryRate?.toString(), r);
  }
  return out;
}
