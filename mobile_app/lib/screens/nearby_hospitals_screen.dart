import 'package:flutter/material.dart';
import 'package:latlong2/latlong.dart';
import 'package:url_launcher/url_launcher.dart';

import '../models/hospital.dart';
import '../services/api_service.dart';
import '../services/location_service.dart';
import '../widgets/emergency_map.dart';

class NearbyHospitalsScreen extends StatefulWidget {
  const NearbyHospitalsScreen({super.key});
  @override
  State<NearbyHospitalsScreen> createState() => _NearbyHospitalsScreenState();
}

class _NearbyHospitalsScreenState extends State<NearbyHospitalsScreen> {
  LatLng? _me;
  List<Hospital> _list = [];
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final p = await LocationService.current();
      final list = await ApiService.instance.nearbyHospitals(p.latitude, p.longitude);
      setState(() {
        _me = LatLng(p.latitude, p.longitude);
        _list = list;
      });
    } catch (e) {
      setState(() => _error = e.toString());
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Nearby hospitals')),
      body: _error != null
          ? Center(child: Text(_error!))
          : _me == null
              ? const Center(child: CircularProgressIndicator())
              : Column(children: [
                  SizedBox(
                    height: 260,
                    child: EmergencyMap(patient: _me!, extraHospitals: [for (final h in _list) LatLng(h.latitude, h.longitude)]),
                  ),
                  Expanded(
                    child: ListView(children: [
                      for (final h in _list)
                        ListTile(
                          leading: const Icon(Icons.local_hospital, color: Colors.red),
                          title: Text(h.name),
                          subtitle: Text('${h.distanceKm?.toStringAsFixed(1)} km • ${h.levelDisplay}\n'
                              'Beds: ${h.availableBeds} • ICU: ${h.icuAvailable}'),
                          isThreeLine: true,
                          trailing: h.phone.isEmpty
                              ? null
                              : IconButton(icon: const Icon(Icons.call), onPressed: () => launchUrl(Uri.parse('tel:${h.phone}'))),
                        ),
                    ]),
                  ),
                ]),
    );
  }
}
