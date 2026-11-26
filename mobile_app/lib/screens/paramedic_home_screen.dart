import 'dart:async';

import 'package:flutter/material.dart';
import 'package:geolocator/geolocator.dart';
import 'package:latlong2/latlong.dart';
import 'package:url_launcher/url_launcher.dart';

import '../models/emergency.dart';
import '../services/api_service.dart';
import '../services/location_service.dart';
import '../widgets/emergency_map.dart';
import '../widgets/priority_badge.dart';
import 'login_screen.dart';

/// Ambulance crew: shares GPS, receives assignments, updates trip status.
class ParamedicHomeScreen extends StatefulWidget {
  const ParamedicHomeScreen({super.key});
  @override
  State<ParamedicHomeScreen> createState() => _ParamedicHomeScreenState();
}

class _ParamedicHomeScreenState extends State<ParamedicHomeScreen> {
  static const nextStep = {
    'assigned': ('en_route', 'Start - en route'),
    'en_route': ('on_scene', 'Arrived on scene'),
    'on_scene': ('transporting', 'Patient loaded - transporting'),
    'transporting': ('arrived', 'Arrived at hospital'),
    'arrived': ('completed', 'Complete mission'),
  };

  Map<String, dynamic>? _ambulance;
  Emergency? _job;
  StreamSubscription<Position>? _gps;
  Timer? _poll;
  String? _error;

  @override
  void initState() {
    super.initState();
    _init();
  }

  Future<void> _init() async {
    try {
      _ambulance = await ApiService.instance.myAmbulance();
      _gps = LocationService.stream().listen(
          (p) => ApiService.instance.updateAmbulanceLocation(p.latitude, p.longitude).catchError((_) {}));
      await _refresh();
      _poll = Timer.periodic(const Duration(seconds: 6), (_) => _refresh());
    } catch (e) {
      setState(() => _error = e.toString());
    }
  }

  Future<void> _refresh() async {
    final jobs = await ApiService.instance.myEmergencies(activeOnly: true);
    if (mounted) setState(() => _job = jobs.isEmpty ? null : jobs.first);
  }

  Future<void> _advance() async {
    final step = nextStep[_job!.status];
    if (step == null) return;
    await ApiService.instance.setStatus(_job!.id, step.$1);
    await _refresh();
  }

  @override
  void dispose() {
    _gps?.cancel();
    _poll?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final j = _job;
    return Scaffold(
      appBar: AppBar(title: Text('🚑 ${_ambulance?['plate_number'] ?? 'Ambulance'}'), actions: [
        IconButton(
          icon: const Icon(Icons.logout),
          onPressed: () async {
            await ApiService.instance.logout();
            if (!context.mounted) return;
            Navigator.pushReplacement(context, MaterialPageRoute(builder: (_) => const LoginScreen()));
          },
        ),
      ]),
      body: _error != null
          ? Center(child: Padding(padding: const EdgeInsets.all(24), child: Text(_error!)))
          : j == null
              ? const Center(child: Text('No active assignment.\nYour location is being shared.', textAlign: TextAlign.center))
              : Column(children: [
                  Expanded(
                    child: EmergencyMap(
                      patient: j.latitude != null ? LatLng(j.latitude!, j.longitude!) : null,
                      patientLabel: j.locationLabel,
                      ambulance: j.ambulanceLat != null ? LatLng(j.ambulanceLat!, j.ambulanceLng!) : null,
                      hospital: j.hospital != null ? LatLng(j.hospital!.latitude, j.hospital!.longitude) : null,
                    ),
                  ),
                  Padding(
                    padding: const EdgeInsets.all(16),
                    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      Row(children: [
                        Expanded(child: Text('#${j.id} ${j.typeDisplay}', style: const TextStyle(fontSize: 20, fontWeight: FontWeight.bold))),
                        PriorityBadge(j.priority),
                      ]),
                      Text('Status: ${j.statusDisplay}'),
                      Text('Patient: ${j.patientName} ${j.patientPhone}'),
                      if (j.hospital != null) Text('Take to: ${j.hospital!.name}'),
                      const SizedBox(height: 12),
                      Row(children: [
                        if (j.patientPhone.isNotEmpty)
                          IconButton.outlined(icon: const Icon(Icons.call), onPressed: () => launchUrl(Uri.parse('tel:${j.patientPhone}'))),
                        IconButton.outlined(
                          icon: const Icon(Icons.navigation),
                          onPressed: () => launchUrl(Uri.parse(
                              'https://www.google.com/maps/dir/?api=1&destination=${j.latitude},${j.longitude}')),
                        ),
                        const SizedBox(width: 8),
                        if (nextStep[j.status] != null)
                          Expanded(child: FilledButton(onPressed: _advance, child: Text(nextStep[j.status]!.$2))),
                      ]),
                    ]),
                  ),
                ]),
    );
  }
}
