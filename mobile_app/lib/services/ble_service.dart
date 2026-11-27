import 'dart:async';

import 'package:flutter_blue_plus/flutter_blue_plus.dart';

import 'gatt_parsers.dart';

/// One standard health profile the app can read from a real Bluetooth device.
class HealthProfile {
  final String kind; // backend MedicalDevice.Kind
  final String label;
  final Guid service;
  final List<Guid> characteristics;
  final Map<String, num>? Function(List<int>) parse;
  const HealthProfile(this.kind, this.label, this.service, this.characteristics, this.parse);
}

final profiles = <HealthProfile>[
  HealthProfile('thermometer', 'Thermometer', Guid('1809'), [Guid('2A1C')], parseTemperature),
  HealthProfile('blood_pressure', 'Blood pressure monitor', Guid('1810'), [Guid('2A35')], parseBloodPressure),
  HealthProfile('heart_rate', 'Heart-rate sensor', Guid('180D'), [Guid('2A37')], parseHeartRate),
  HealthProfile('pulse_oximeter', 'Pulse oximeter', Guid('1822'), [Guid('2A5E'), Guid('2A5F')], parsePlx),
];

/// A measurement decoded from a REAL device. There is no code path that produces one without a device.
class BleMeasurement {
  final Map<String, num> values;
  final Map<String, dynamic> device; // sent to the backend for identification
  final DateTime at;
  BleMeasurement(this.values, this.device) : at = DateTime.now();
}

class BleService {
  BleService._();
  static final instance = BleService._();

  BluetoothDevice? _device;
  HealthProfile? _profile;
  final _subs = <StreamSubscription>[];
  final _measurements = StreamController<BleMeasurement>.broadcast();

  Stream<BleMeasurement> get measurements => _measurements.stream;
  BluetoothDevice? get device => _device;
  HealthProfile? get profile => _profile;
  bool get isConnected => _device?.isConnected ?? false;

  Future<String?> unavailableReason() async {
    if (!await FlutterBluePlus.isSupported) return 'This phone does not support Bluetooth Low Energy.';
    final s = await FlutterBluePlus.adapterState.first;
    if (s == BluetoothAdapterState.unauthorized) return 'Bluetooth permission was denied.';
    if (s != BluetoothAdapterState.on && s != BluetoothAdapterState.unknown) return 'Please turn on Bluetooth.';
    return null;
  }

  /// Scans only for devices that advertise one of the supported health services.
  /// [kinds] narrows the scan (e.g. ['thermometer']).
  Stream<List<ScanResult>> scan({Duration timeout = const Duration(seconds: 15), List<String>? kinds}) {
    FlutterBluePlus.startScan(
        withServices: [for (final p in profiles) if (kinds == null || kinds.contains(p.kind)) p.service], timeout: timeout);
    return FlutterBluePlus.scanResults;
  }

  Future<void> stopScan() => FlutterBluePlus.stopScan();

  /// Connects and subscribes to the first supported profile (only [kinds] if given).
  Future<HealthProfile> connect(BluetoothDevice d,
      {Duration timeout = const Duration(seconds: 20), List<String>? kinds}) async {
    await disconnect();
    await FlutterBluePlus.stopScan();
    // Educational / non-commercial project -> FlutterBluePlus nonprofit license.
    await d.connect(license: License.nonprofit, timeout: timeout);
    try {
      final services = await d.discoverServices();
      for (final p in profiles.where((p) => kinds == null || kinds.contains(p.kind))) {
        final svc = services.where((s) => s.uuid == p.service).firstOrNull;
        if (svc == null) continue;
        var subscribed = false;
        for (final c in svc.characteristics.where((c) => p.characteristics.contains(c.uuid))) {
          if (!(c.properties.notify || c.properties.indicate)) continue;
          _subs.add(c.onValueReceived.listen((bytes) {
            final v = p.parse(bytes);
            if (v != null) {
              _measurements.add(BleMeasurement(v, {
                'device_identifier': d.remoteId.str,
                'name': d.platformName.isEmpty ? p.label : d.platformName,
                'kind': p.kind,
              }));
            }
          }));
          d.cancelWhenDisconnected(_subs.last);
          await c.setNotifyValue(true);
          subscribed = true;
        }
        if (subscribed) {
          _device = d;
          _profile = p;
          return p;
        }
      }
      throw Exception('Connected, but the device offers no supported health measurement.');
    } catch (_) {
      await d.disconnect();
      rethrow;
    }
  }

  Future<void> disconnect() async {
    for (final s in _subs) {
      await s.cancel();
    }
    _subs.clear();
    await _device?.disconnect();
    _device = null;
    _profile = null;
  }
}
