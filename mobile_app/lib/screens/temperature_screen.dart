import 'dart:async';
import 'dart:math';

import 'package:flutter/material.dart';
import 'package:flutter_blue_plus/flutter_blue_plus.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../services/ble_service.dart';
import '../services/measurement_outbox.dart';
import '../services/voice_service.dart';
import '../theme.dart';
import '../widgets/measure_widgets.dart';

/// Body temperature from a REAL Bluetooth thermometer (Health Thermometer profile 0x1809), at most 60 seconds,
/// sent to the operator automatically.
///
///   Start -> 60 s countdown -> reuse the connected / remembered thermometer (no scan), else scan for
///   thermometers only -> connect -> wait for the thermometer's own measurement -> validate -> show + send.
/// A value is only ever the one decoded from the thermometer. No reading in time -> FAILED (timeout).
class TemperatureScreen extends StatefulWidget {
  const TemperatureScreen({super.key});
  @override
  State<TemperatureScreen> createState() => _TemperatureScreenState();
}

enum _Phase { checking, ready, unavailable, connecting, waiting, done }

/// Plausible body temperature from a body thermometer. Outside it -> INCONCLUSIVE (never shown as valid).
const minBodyC = 30.0, maxBodyC = 43.0;

MeasureResult validateTemperature(num? c) {
  if (c == null) {
    return const MeasureResult(MeasureOutcome.inconclusive, unit: '°C', reason: 'The thermometer sent no usable value.');
  }
  if (c < minBodyC || c > maxBodyC) {
    return MeasureResult(MeasureOutcome.inconclusive, unit: '°C',
        reason: 'The thermometer reported $c °C, outside the body range $minBodyC-$maxBodyC °C. Please measure again.');
  }
  return MeasureResult(MeasureOutcome.measured, value: double.parse(c.toStringAsFixed(1)), unit: '°C');
}

class _TemperatureScreenState extends State<TemperatureScreen> {
  static const _prefKey = 'last_thermometer_id';
  final _ble = BleService.instance;
  _Phase _phase = _Phase.checking;
  String? _problem;
  String _status = '';
  String? _remembered;
  final _clock = Stopwatch();
  Timer? _tick, _deadline;
  StreamSubscription<BleMeasurement>? _sub;
  Map<String, dynamic>? _device;
  MeasureResult? _result;
  ValueNotifier<SendState>? _send;
  int _run = 0; // invalidates callbacks of a previous attempt

  @override
  void initState() {
    super.initState();
    _check();
  }

  Future<void> _check() async {
    setState(() => _phase = _Phase.checking);
    final reason = await _ble.unavailableReason();
    _remembered = (await SharedPreferences.getInstance()).getString(_prefKey);
    if (!mounted) return;
    setState(() {
      _problem = reason;
      _phase = reason == null ? _Phase.ready : _Phase.unavailable;
    });
  }

  Duration get _left => measurementDeadline - _clock.elapsed;

  Future<void> _start() async {
    final run = ++_run;
    _result = null;
    _send = null;
    _device = null;
    _clock
      ..reset()
      ..start();
    setState(() {
      _phase = _Phase.connecting;
      _status = 'Looking for your thermometer...';
    });
    _tick = Timer.periodic(const Duration(milliseconds: 500), (_) => mounted ? setState(() {}) : null);
    _deadline = Timer(measurementDeadline, () => _timeout(run));
    // Listen before connecting: some thermometers send the measurement right after the connection.
    await _sub?.cancel();
    _sub = _ble.measurements.listen((m) {
      if (run != _run || _phase == _Phase.done || m.device['kind'] != 'thermometer') return;
      if (!m.values.containsKey('temperature_c')) return;
      _finish(validateTemperature(m.values['temperature_c']), m.device);
    });
    try {
      final d = await _connect(run);
      if (run != _run || _phase == _Phase.done) return;
      if (d == null) return; // timeout already reported
      (await SharedPreferences.getInstance()).setString(_prefKey, d.remoteId.str);
      setState(() {
        _phase = _Phase.waiting;
        _status = 'Connected to ${_device?['name'] ?? 'thermometer'}. Take your temperature now, '
            'as the thermometer\'s instructions say.';
      });
      VoiceService.instance.say('Thermometer connected. Take your temperature now.');
    } catch (e) {
      if (run != _run || _phase == _Phase.done) return;
      _finish(MeasureResult(MeasureOutcome.failed, unit: '°C',
          reason: 'Could not connect to the thermometer: ${'$e'.replaceFirst('Exception: ', '')}'));
    }
  }

  /// Fastest path first: already connected -> remembered device -> devices connected in the phone -> scan.
  Future<BluetoothDevice?> _connect(int run) async {
    final cur = _ble.device;
    if (cur != null && _ble.isConnected && _ble.profile?.kind == 'thermometer') {
      _device = _info(cur);
      return cur;
    }
    Duration budget(int maxSeconds) => Duration(seconds: max(3, min(maxSeconds, _left.inSeconds - 8)));
    final tried = <String>{};
    Future<BluetoothDevice?> attempt(BluetoothDevice d, int seconds) async {
      if (!tried.add(d.remoteId.str) || run != _run) return null;
      try {
        await _ble.connect(d, timeout: budget(seconds), kinds: const ['thermometer']);
        _device = _info(d);
        return d;
      } catch (_) {
        return null;
      }
    }

    if (_remembered != null) {
      setState(() => _status = 'Connecting to your usual thermometer...');
      final d = await attempt(BluetoothDevice.fromId(_remembered!), 12);
      if (d != null) return d;
    }
    try {
      for (final d in await FlutterBluePlus.systemDevices([Guid('1809')])) {
        final c = await attempt(d, 10);
        if (c != null) return c;
      }
    } catch (_) {/* not supported on every platform */}
    while (run == _run && _phase != _Phase.done && _left.inSeconds > 10) {
      if (mounted) setState(() => _status = 'Searching for a Bluetooth thermometer... Turn it on.');
      final found = Completer<ScanResult?>();
      final scanFor = Duration(seconds: min(15, _left.inSeconds - 8));
      final sub = _ble.scan(timeout: scanFor, kinds: const ['thermometer']).listen((rs) {
        final fresh = rs.where((r) => !tried.contains(r.device.remoteId.str)).toList()
          ..sort((a, b) => b.rssi.compareTo(a.rssi));
        if (fresh.isNotEmpty && !found.isCompleted) found.complete(fresh.first);
      });
      final r = await found.future.timeout(scanFor, onTimeout: () => null);
      await sub.cancel();
      await _ble.stopScan();
      if (r == null) continue;
      if (mounted) setState(() => _status = 'Connecting to ${r.device.platformName.isEmpty ? 'thermometer' : r.device.platformName}...');
      final c = await attempt(r.device, 12);
      if (c != null) return c;
    }
    return null;
  }

  Map<String, dynamic> _info(BluetoothDevice d) => {
        'device_identifier': d.remoteId.str,
        'name': d.platformName.isEmpty ? 'Bluetooth thermometer' : d.platformName,
        'kind': 'thermometer',
      };

  void _timeout(int run) {
    if (run != _run || _phase == _Phase.done) return;
    final connected = _phase == _Phase.waiting;
    _finish(MeasureResult(MeasureOutcome.failed, unit: '°C',
        reason: connected
            ? 'Timeout: the thermometer was connected but sent no measurement within 60 seconds.'
            : 'Timeout: no compatible Bluetooth thermometer could be connected within 60 seconds.'));
  }

  Future<void> _finish(MeasureResult result, [Map<String, dynamic>? device]) async {
    if (_phase == _Phase.done) return;
    _tick?.cancel();
    _deadline?.cancel();
    final duration = _clock.elapsed;
    _clock.stop();
    await _sub?.cancel();
    await _ble.stopScan();
    setState(() {
      _phase = _Phase.done;
      _result = result;
    });
    VoiceService.instance.say(result.valid
        ? 'Temperature ${result.value} degrees. Sending to the operator.'
        : 'The temperature measurement did not succeed. You can try again.');
    final dev = device ?? _device;
    final s = await MeasurementOutbox.instance.submit({
      'client_id': MeasurementOutbox.newClientId(),
      'kind': 'temperature',
      'status': result.outcome.name,
      'value': result.valid ? result.value : null,
      'source': 'ble_thermometer',
      'method': 'ble',
      'measured_at': DateTime.now().toUtc().toIso8601String(),
      'duration_s': double.parse((duration.inMilliseconds / 1000).toStringAsFixed(1)),
      if (!result.valid) 'failure_reason': result.reason,
      'device': dev ?? {'name': 'no thermometer connected'},
    });
    if (mounted) setState(() => _send = s);
  }

  @override
  void dispose() {
    _run++;
    _tick?.cancel();
    _deadline?.cancel();
    _sub?.cancel();
    _ble.stopScan();
    super.dispose(); // the thermometer stays connected for the next measurement (no re-scan)
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Body temperature')),
      body: ListView(padding: const EdgeInsets.all(16), children: [
        Card(
          child: Padding(
            padding: const EdgeInsets.all(14),
            child: Text(
                'Uses your Bluetooth thermometer. The temperature it measures is sent to the emergency operator '
                'automatically. If your thermometer needs longer than 60 seconds, follow its instructions and try '
                'again - a reading is never guessed.',
                style: TextStyle(color: Colors.grey.shade800)),
          ),
        ),
        const SizedBox(height: 16),
        switch (_phase) {
          _Phase.checking => const Center(child: Padding(padding: EdgeInsets.all(24), child: Text('Checking Bluetooth...'))),
          _Phase.unavailable => Column(children: [
              Text(_problem ?? '', style: const TextStyle(color: AppColors.emergency, fontSize: 16)),
              const SizedBox(height: 12),
              OutlinedButton.icon(onPressed: _check, icon: const Icon(Icons.refresh), label: const Text('Try again')),
            ]),
          _Phase.ready => Column(children: [
              const StepLine('Bluetooth is on', done: true),
              StepLine(_remembered == null ? 'Turn on your thermometer' : 'Turn on your usual thermometer'),
              const StepLine('Press Start, then take your temperature'),
              const SizedBox(height: 16),
              FilledButton.icon(
                  style: FilledButton.styleFrom(minimumSize: const Size.fromHeight(56)),
                  onPressed: _start,
                  icon: const Icon(Icons.thermostat),
                  label: const Text('Start measurement', style: TextStyle(fontSize: 18))),
            ]),
          _Phase.connecting || _Phase.waiting => Column(children: [
              CountdownRing(remaining: _left, caption: _status),
              const SizedBox(height: 12),
              StepLine('Connect thermometer', done: _phase == _Phase.waiting, active: _phase == _Phase.connecting),
              StepLine('Receive the measurement', active: _phase == _Phase.waiting),
            ]),
          _Phase.done => Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
              const StepLine('Measurement completed', done: true),
              ResultCard(result: _result!, label: 'Body temperature',
                  note: _result!.valid ? 'Measured by ${_device?['name'] ?? 'your thermometer'} (Bluetooth).' : null),
              if (_send != null) SendStatusCard(state: _send!),
              const SizedBox(height: 8),
              OutlinedButton.icon(onPressed: _check, icon: const Icon(Icons.replay),
                  label: Text(_result!.valid ? 'Measure again' : 'Try again')),
            ]),
        },
      ]),
    );
  }
}
