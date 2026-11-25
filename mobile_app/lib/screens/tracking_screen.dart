import 'dart:async';

import 'package:flutter/material.dart';
import 'package:latlong2/latlong.dart';

import '../models/emergency.dart';
import '../services/api_service.dart';
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
    super.dispose();
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
                    Text('🚑 Ambulance ${e.ambulancePlate}  •  ETA ~${e.etaMinutes?.toStringAsFixed(0) ?? '?'} min'),
                  if (e.ambulancePlate == null && e.isActive)
                    const Text('Waiting for an available ambulance. An operator has been alerted.'),
                  if (e.hospital != null) Text('🏥 Destination: ${e.hospital!.name}'),
                  const SizedBox(height: 12),
                  if (e.isActive && ['pending', 'assigned', 'en_route'].contains(e.status))
                    OutlinedButton(onPressed: _cancel, child: const Text('Cancel request')),
                ]),
              ),
            ]),
    );
  }
}
