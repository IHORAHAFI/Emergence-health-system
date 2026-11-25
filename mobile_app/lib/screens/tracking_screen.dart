import 'dart:async';

import 'package:flutter/material.dart';
import 'package:latlong2/latlong.dart';

import '../models/emergency.dart';
import '../services/api_service.dart';
import '../services/ble_service.dart';
import '../widgets/common.dart';
import 'ble_connect_screen.dart';
import '../widgets/emergency_map.dart';
import '../widgets/priority_badge.dart';

/// Patient-side live status tracking (polls the backend every 5 seconds).
class TrackingScreen extends StatefulWidget {
  final int emergencyId;
  const TrackingScreen({super.key, required this.emergencyId});
  @override
  State<TrackingScreen> createState() => _TrackingScreenState();
}

class _TrackingScreenState extends State<TrackingScreen> {
  Emergency? _em;
  Timer? _timer;
  StreamSubscription<BleMeasurement>? _ble;
  String? _bleStatus;

  @override
  void initState() {
    super.initState();
    _load();
    _timer = Timer.periodic(const Duration(seconds: 5), (_) => _load());
  }

  Future<void> _load() async {
    try {
      final e = await ApiService.instance.getEmergency(widget.emergencyId);
      if (mounted) setState(() => _em = e);
      if (!e.isActive) _timer?.cancel();
    } catch (_) {}
  }

  @override
  void dispose() {
    _timer?.cancel();
    _ble?.cancel();
    if (_ble != null) BleService.instance.disconnect();
    super.dispose();
  }

  /// Share REAL readings from the patient's Bluetooth device with the responding team (explicit consent).
  Future<void> _shareVitals() async {
    final e = _em!;
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        icon: const Icon(Icons.monitor_heart),
        title: const Text('Share vital signs?'),
        content: const Text('Measurements from your device will be sent to the operators, ambulance crew and '
            'hospital handling this emergency. You can stop at any time.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Not now')),
          FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('I agree')),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    try {
      await ApiService.instance.giveMonitoringConsent(e.reference);
    } catch (err) {
      if (mounted) toast(context, err, error: true);
      return;
    }
    if (!mounted) return;
    final p = await Navigator.push<HealthProfile>(context, MaterialPageRoute(builder: (_) => const BleConnectScreen()));
    if (p == null || !mounted) return;
    setState(() => _bleStatus = '${p.label} connected');
    _ble = BleService.instance.measurements.listen((m) async {
      try {
        await ApiService.instance.sendEmergencyReading(e.reference, m.values, m.device);
        if (mounted) setState(() => _bleStatus = 'Sent ${m.values.values.join(' / ')} · ${ago(m.at)}');
      } catch (err) {
        if (mounted) toast(context, err, error: true);
      }
    });
  }

  Future<void> _stopVitals() async {
    await _ble?.cancel();
    _ble = null;
    await BleService.instance.disconnect();
    if (mounted) setState(() => _bleStatus = null);
  }

  Future<void> _cancel() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Cancel request?'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('No')),
          TextButton(onPressed: () => Navigator.pop(c, true), child: const Text('Yes, cancel')),
        ],
      ),
    );
    if (ok == true) {
      await ApiService.instance.setStatus(widget.emergencyId, 'cancelled');
      _load();
    }
  }

  @override
  Widget build(BuildContext context) {
    final e = _em;
    return Scaffold(
      appBar: AppBar(title: Text('Emergency #${widget.emergencyId}')),
      body: e == null
          ? const Center(child: CircularProgressIndicator())
          : Column(children: [
              Expanded(
                child: EmergencyMap(
                  patient: LatLng(e.latitude, e.longitude),
                  ambulance: e.ambulanceLat != null ? LatLng(e.ambulanceLat!, e.ambulanceLng!) : null,
                  hospital: e.hospital != null ? LatLng(e.hospital!.latitude, e.hospital!.longitude) : null,
                ),
              ),
              Padding(
                padding: const EdgeInsets.all(16),
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Row(children: [
                    Expanded(child: Text(e.statusDisplay, style: const TextStyle(fontSize: 22, fontWeight: FontWeight.bold))),
                    PriorityBadge(e.priority),
                  ]),
                  const SizedBox(height: 8),
                  if (e.ambulancePlate != null)
                    Card(
                      child: ListTile(
                        leading: const CircleAvatar(child: Icon(Icons.local_shipping)),
                        title: Text('Ambulance ${e.ambulancePlate}', style: const TextStyle(fontWeight: FontWeight.w700)),
                        subtitle: Text('ETA ~${e.etaMinutes?.toStringAsFixed(0) ?? '?'} min'),
                      ),
                    ),
                  if (e.ambulancePlate == null && e.isActive)
                    const Text('Waiting for an available ambulance. An operator has been alerted.'),
                  if (e.hospital != null) Text('🏥 Destination: ${e.hospital!.name}'),
                  const SizedBox(height: 12),
                  if (e.isActive && e.reference.isNotEmpty)
                    _ble == null
                        ? OutlinedButton.icon(
                            onPressed: _shareVitals, icon: const Icon(Icons.bluetooth), label: const Text('Share vital signs'))
                        : ListTile(
                            contentPadding: EdgeInsets.zero,
                            leading: const Icon(Icons.bluetooth_connected, color: Colors.green),
                            title: Text(_bleStatus ?? 'Connected'),
                            trailing: TextButton(onPressed: _stopVitals, child: const Text('Stop')),
                          ),
                  const SizedBox(height: 8),
                  if (e.isActive && ['pending', 'assigned', 'en_route'].contains(e.status))
                    OutlinedButton(onPressed: _cancel, child: const Text('Cancel request')),
                ]),
              ),
            ]),
    );
  }
}
