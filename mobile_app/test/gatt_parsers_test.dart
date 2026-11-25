import 'package:flutter_test/flutter_test.dart';
import 'package:smart_emergency/models/test_request.dart';
import 'package:smart_emergency/models/vital_reading.dart';
import 'package:smart_emergency/services/gatt_parsers.dart';

void main() {
  test('SFLOAT decoding and special values', () {
    expect(sfloat(0x62, 0x00), 98); // 98 * 10^0
    expect(sfloat(0xFF, 0x07), isNull); // NaN
    expect(sfloat(0x6D, 0xF1), closeTo(36.5, 1e-9)); // 365 * 10^-1
  });

  test('temperature in Celsius and Fahrenheit', () {
    // flags 0 (C), mantissa 372, exponent -1 -> 37.2
    expect(parseTemperature([0x00, 0x74, 0x01, 0x00, 0xFF]), {'temperature_c': 37.2});
    // flags 1 (F), 98.6 F -> 37.0 C : mantissa 986, exp -1
    expect(parseTemperature([0x01, 0xDA, 0x03, 0x00, 0xFF]), {'temperature_c': 37.0});
  });

  test('blood pressure with pulse rate', () {
    // flags 0x04 (mmHg, pulse present), sys 120, dia 80, MAP 93, pulse 72
    expect(parseBloodPressure([0x04, 120, 0, 80, 0, 93, 0, 72, 0]),
        {'systolic_mmhg': 120, 'diastolic_mmhg': 80, 'heart_rate_bpm': 72});
  });

  test('heart rate 8/16 bit and no-contact zero', () {
    expect(parseHeartRate([0x00, 75]), {'heart_rate_bpm': 75});
    expect(parseHeartRate([0x01, 0x2C, 0x01]), {'heart_rate_bpm': 300});
    expect(parseHeartRate([0x00, 0]), isNull);
  });

  test('pulse oximeter', () {
    expect(parsePlx([0x00, 97, 0, 70, 0]), {'spo2_pct': 97, 'heart_rate_bpm': 70});
  });

  test('simulated readings stay flagged in latest values', () {
    final rs = [
      VitalReading.fromJson({'id': 2, 'measured_at': '2026-11-25T10:00:00Z', 'source': 'simulated', 'simulated': true, 'heart_rate_bpm': 90}),
      VitalReading.fromJson({'id': 1, 'measured_at': '2026-11-25T09:00:00Z', 'source': 'bluetooth', 'simulated': false, 'heart_rate_bpm': 70, 'spo2_pct': 98}),
    ];
    final l = latestByVital(rs);
    expect(l['heart_rate']!.value, '90');
    expect(l['heart_rate']!.simulated, isTrue);
    expect(l['spo2']!.simulated, isFalse);
  });

  test('test request parsing', () {
    final r = TestRequest.fromJson({
      'reference': 'TST-1', 'status': 'testing', 'status_display': 'Testing - measurement in progress',
      'tests': ['heart_rate'], 'test_names': 'Heart Rate', 'requested_at': '2026-11-25T09:00:00Z',
      'summary': {'heart_rate_bpm': {'label': 'Heart rate', 'value': 80, 'display': '80', 'simulated': true, 'at': '2026-11-25T09:01:00Z'}},
    });
    expect(r.statusDisplay, 'Testing');
    expect(r.isTesting, isTrue);
    expect(r.hasSimulated, isTrue);
  });
}
