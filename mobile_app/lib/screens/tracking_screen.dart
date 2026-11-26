import 'dart:async';

import 'package:flutter/material.dart';
import 'package:geolocator/geolocator.dart';
import 'package:latlong2/latlong.dart';

import '../models/emergency.dart';
import '../services/api_service.dart';
import '../services/ble_service.dart';
import '../services/location_service.dart';
import '../theme.dart';
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
  StreamSubscription<Position>? _gps;
  String? _gpsStatus;
  bool _gpsError = false;

  @override
  void initState() {
    super.initState();
    _load();
    _timer = Timer.periodic(const Duration(seconds: 5), (_) => _load());
  }

  Future<void> _load() async {
    try {
      final e = await ApiService.instance.getEmergency(widget.emergencyId);
      final firstLoad = _em == null;
      if (mounted) setState(() => _em = e);
      if (!e.isActive) {
        _timer?.cancel();
        _stopGps('The emergency has ended - location sharing stopped.');
      } else if (firstLoad && e.raw['location_source'] == 'gps') {
        _startGps(); // the patient chose GPS when sending the request: keep it live (visible, with Stop)
      }
    } catch (_) {}
  }

  /// Live GPS while this screen is open and the emergency is active. Uses the phone's GPS with permission;
  /// sends only after moving 20 m (LocationService.stream). Android/iOS stop it when the app is in the background.
  Future<void> _startGps() async {
    setState(() {
      _gpsStatus = 'Finding your location…';
      _gpsError = false;
    });
    try {
      final first = await LocationService.current(); // asks permission, explains failures
      await _send(first);
      await _gps?.cancel();
      _gps = LocationService.stream().listen(_send, onError: (e) => _gpsFail('$e'));
    } catch (e) {
      _gpsFail(e.toString().replaceFirst('Exception: ', ''));
    }
  }

  Future<void> _send(Position p) async {
    final em = _em;
    if (em == null || !em.isActive) return;
    try {
      await ApiService.instance.shareLocation(em.id, p.latitude, p.longitude, accuracy: p.accuracy, at: p.timestamp);
      if (mounted) {
        setState(() {
          _gpsStatus = 'Sharing live location (±${p.accuracy.round()} m)';
          _gpsError = false;
        });
      }
    } catch (e) {
      _gpsFail('Location not sent: $e');
    }
  }

  void _gpsFail(String why) {
    if (mounted) {
      setState(() {
        _gpsStatus = why;
        _gpsError = true;
      });
    }
  }

  void _stopGps([String? why]) {
    _gps?.cancel();
    _gps = null;
    if (mounted && why != null) setState(() => _gpsStatus = why);
  }

  @override
  void dispose() {
    _gps?.cancel();
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
                  patient: e.latitude != null ? LatLng(e.latitude!, e.longitude!) : null,
                  patientLabel: e.locationLabel,
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
                  _steps(e.operatorLabel),
                  if (e.isActive)
                    Semantics(
                      liveRegion: true,
                      child: Padding(
                        padding: const EdgeInsets.only(top: 6),
                        child: Row(children: [
                          Icon(_gps != null ? Icons.my_location : Icons.location_off,
                              size: 18, color: _gpsError ? Colors.red : (_gps != null ? Colors.green : Colors.black45)),
                          const SizedBox(width: 6),
                          Expanded(
                            child: Text(_gpsStatus ?? '${e.locationLabel} - share your live location so the ambulance can find you.',
                                style: TextStyle(fontSize: 13, color: _gpsError ? Colors.red : null)),
                          ),
                          _gps == null
                              ? TextButton(onPressed: _startGps, child: const Text('Share'))
                              : TextButton(onPressed: () => _stopGps('Location sharing stopped.'), child: const Text('Stop')),
                        ]),
                      ),
                    ),
                  if (e.acceptedBy != null)
                    Padding(
                      padding: const EdgeInsets.only(top: 4),
                      child: Text('Handled by ${e.acceptedBy}', style: const TextStyle(color: Colors.black54)),
                    ),
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
                  if (e.isActive)
                    OutlinedButton.icon(
                        onPressed: _message, icon: const Icon(Icons.chat_outlined), label: const Text('Message the team')),
                  const SizedBox(height: 8),
                  if (e.isActive && ['pending', 'assigned', 'en_route'].contains(e.status))
                    OutlinedButton(onPressed: _cancel, child: const Text('Cancel request')),
                ]),
              ),
            ]),
    );
  }

  /// PENDING -> ACCEPTED -> IN PROGRESS -> COMPLETED (label computed by the server).
  Widget _steps(String label) {
    const steps = ['PENDING', 'ACCEPTED', 'IN PROGRESS', 'COMPLETED'];
    if (label == 'CANCELLED') return const StatusChip('cancelled', 'CANCELLED');
    final idx = steps.indexOf(label);
    return Row(children: [
      for (var i = 0; i < steps.length; i++)
        Expanded(
          child: Column(children: [
            Container(height: 6, margin: const EdgeInsets.symmetric(horizontal: 2),
                decoration: BoxDecoration(color: i <= idx ? AppColors.primary : const Color(0xFFE4E7EC),
                    borderRadius: BorderRadius.circular(3))),
            const SizedBox(height: 4),
            Text(steps[i], style: TextStyle(fontSize: 10, fontWeight: i == idx ? FontWeight.w800 : FontWeight.w500)),
          ]),
        ),
    ]);
  }

  Future<void> _message() async {
    final c = TextEditingController();
    final text = await showDialog<String>(
      context: context,
      builder: (d) => AlertDialog(
        title: const Text('Message the emergency team'),
        content: TextField(controller: c, maxLines: 3, maxLength: 500, autofocus: true),
        actions: [
          TextButton(onPressed: () => Navigator.pop(d), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(d, c.text.trim()), child: const Text('Send')),
        ],
      ),
    );
    if (text == null || text.isEmpty) return;
    try {
      await ApiService.instance.sendEmergencyMessage(widget.emergencyId, text);
      if (mounted) toast(context, 'Message sent');
    } catch (err) {
      if (mounted) toast(context, err, error: true);
    }
  }
}
