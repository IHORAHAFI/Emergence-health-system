/// A patient's medical test request (mirrors patient_monitoring.test_requests.as_dict).
class TestResult {
  final String key, label, explain, display, unit, source, device;
  final dynamic value;
  final bool simulated;
  final DateTime? at;
  final Map<String, dynamic> flags;

  TestResult.fromJson(this.key, Map<String, dynamic> j)
      : label = j['label'] ?? key,
        explain = j['explain'] ?? '',
        display = j['display'] ?? '',
        unit = j['unit'] ?? '',
        value = j['value'],
        source = j['source'] ?? '',
        device = j['device'] ?? '',
        simulated = j['simulated'] == true,
        at = DateTime.tryParse(j['at'] ?? '')?.toLocal(),
        flags = Map<String, dynamic>.from(j['flags'] ?? {});
}

class TestRequest {
  final String reference, status, statusDisplay, statusText, testNames;
  final List<String> tests;
  final DateTime requestedAt;
  final DateTime? completedAt;
  final Map<String, TestResult> summary;
  final List<Map<String, dynamic>> readings;
  final String? simulationStatus;

  TestRequest.fromJson(Map<String, dynamic> j)
      : reference = j['reference'],
        status = j['status'],
        statusDisplay = (j['status_display'] ?? j['status']).toString().split(' - ').first,
        statusText = j['status_text'] ?? '',
        testNames = j['test_names'] ?? '',
        tests = List<String>.from(j['tests'] ?? const []),
        requestedAt = DateTime.parse(j['requested_at']).toLocal(),
        completedAt = DateTime.tryParse(j['completed_at'] ?? '')?.toLocal(),
        summary = {
          for (final e in Map<String, dynamic>.from(j['summary'] ?? {}).entries)
            e.key: TestResult.fromJson(e.key, Map<String, dynamic>.from(e.value))
        },
        readings = ((j['readings'] ?? const []) as List).map((e) => Map<String, dynamic>.from(e)).toList(),
        simulationStatus = j['simulation'] is Map ? j['simulation']['status'] : null;

  bool get isOpen => const ['pending', 'accepted', 'testing'].contains(status);
  bool get isTesting => status == 'testing';
  bool get hasSimulated => summary.values.any((r) => r.simulated) || readings.any((r) => r['simulated'] == true);
}
