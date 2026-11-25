import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:latlong2/latlong.dart';

/// OpenStreetMap view showing patient, ambulance and hospital markers.
class EmergencyMap extends StatelessWidget {
  final LatLng patient;
  final LatLng? ambulance;
  final LatLng? hospital;
  final List<LatLng> extraHospitals;

  const EmergencyMap({super.key, required this.patient, this.ambulance, this.hospital, this.extraHospitals = const []});

  Marker _m(LatLng p, String emoji) =>
      Marker(point: p, width: 36, height: 36, child: Text(emoji, style: const TextStyle(fontSize: 28)));

  @override
  Widget build(BuildContext context) {
    return FlutterMap(
      options: MapOptions(initialCenter: patient, initialZoom: 13),
      children: [
        // CARTO basemap (OSM data): tile.openstreetmap.org blocks apps/browsers without a valid Referer/User-Agent.
        TileLayer(
          urlTemplate: 'https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png',
          subdomains: const ['a', 'b', 'c', 'd'],
          userAgentPackageName: 'rw.ac.ur.smart_emergency',
        ),
        PolylineLayer(polylines: [
          if (ambulance != null) Polyline(points: [ambulance!, patient], color: Colors.orange, strokeWidth: 4),
          if (hospital != null) Polyline(points: [patient, hospital!], color: Colors.blue, strokeWidth: 3),
        ]),
        MarkerLayer(markers: [
          for (final h in extraHospitals) _m(h, '🏥'),
          _m(patient, '📍'),
          if (ambulance != null) _m(ambulance!, '🚑'),
          if (hospital != null) _m(hospital!, '🏥'),
        ]),
        // Exact GPS position, always readable even if map tiles fail to load.
        Align(
          alignment: Alignment.topCenter,
          child: Container(
            margin: const EdgeInsets.all(8),
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
            decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(8),
                boxShadow: const [BoxShadow(blurRadius: 4, color: Colors.black26)]),
            child: Text('📍 GPS: ${patient.latitude.toStringAsFixed(5)}, ${patient.longitude.toStringAsFixed(5)}',
                style: const TextStyle(fontWeight: FontWeight.bold)),
          ),
        ),
        const RichAttributionWidget(attributions: [
          TextSourceAttribution('OpenStreetMap contributors'),
          TextSourceAttribution('CARTO'),
        ]),
      ],
    );
  }
}
