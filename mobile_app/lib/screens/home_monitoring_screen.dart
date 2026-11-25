import 'dart:async';

import 'package:flutter/material.dart';

import '../services/api_service.dart';
import '../services/ble_service.dart';
import '../services/voice_service.dart';
import '../theme.dart';
import '../widgets/common.dart';
import 'ble_connect_screen.dart';
import 'checkin_screen.dart';

/// Home monitoring: connect the patient's own Bluetooth device; every REAL measurement is sent to the server,
/// which checks it (age-specific limits + NEWS2) and starts a check-in or escalation if it is concerning.
class HomeMonitoringScreen extends StatefulWidget {
  const HomeMonitoringScreen({super.key});
  @override
  State<HomeMonitoringScreen> createState() => _HomeMonitoringScreenState();
}

class _HomeMonitoringScreenState extends State<HomeMonitoringScreen> {
  StreamSubscription<BleMeasurement>? _sub;
  String? _device, _last;
  final _log = <String>[];

  @override
  void dispose() {
    _sub?.cancel();
    BleService.instance.disconnect();
    super.dispose();
  }

  Future<void> _connect() async {
    final p = await Navigator.push<HealthProfile>(context, MaterialPageRoute(builder: (_) => const BleConnectScreen()));
    if (p == null || !mounted) return;
    setState(() => _device = p.label);
    VoiceService.instance.say('${p.label} connected. Take a measurement.');
    await _sub?.cancel();
    _sub = BleService.instance.measurements.listen((m) async {
      try {
        final r = Map<String, dynamic>.from(
            await ApiService.instance.post('/home-monitoring/readings/', {...m.values, 'device': m.device}));
        final text = m.values.entries.map((e) => '${e.key.split('_').first} ${e.value}').join(', ');
        setState(() {
          _last = 'Sent: $text';
          _log.insert(0, '${TimeOfDay.now().format(context)}  $text');
        });
        final ev = r['event'];
        if (ev is Map && ev['status'] == 'awaiting_response' && mounted) {
          Navigator.push(context, MaterialPageRoute(builder: (_) => CheckInScreen(event: Map<String, dynamic>.from(ev))));
        } else {
          VoiceService.instance.say('Measurement received.');
        }
      } catch (e) {
        if (mounted) toast(context, e, error: true);
      }
    });
  }

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(title: const Text('Home monitoring')),
        body: ListView(padding: const EdgeInsets.all(16), children: [
          const Text('Readings from your own device are checked automatically. If something looks concerning, your '
              'assistant will ask how you feel, and the emergency team is told if you need help or do not answer.',
              style: TextStyle(fontSize: 15)),
          const SizedBox(height: 16),
          if (_device == null)
            FilledButton.icon(onPressed: _connect, icon: const Icon(Icons.bluetooth), label: const Text('Connect device'))
          else
            Card(
              child: ListTile(
                leading: const Icon(Icons.bluetooth_connected, color: AppColors.ok),
                title: Text(_device!),
                subtitle: Text(_last ?? 'Take a measurement on the device'),
                trailing: const SourceBadge(simulated: false),
              ),
            ),
          const SizedBox(height: 16),
          for (final l in _log) Text(l, style: const TextStyle(color: Colors.black54)),
        ]),
      );
}
