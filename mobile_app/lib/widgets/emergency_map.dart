import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:latlong2/latlong.dart';

/// OpenStreetMap view showing patient, ambulance and hospital markers.
/// [patient] is null when no location was shared: no patient marker is drawn (never an invented position).
class EmergencyMap extends StatelessWidget {
  final LatLng? patient;
  final String patientLabel;
  final LatLng? ambulance;
  final LatLng? hospital;
  final List<LatLng> extraHospitals;

  const EmergencyMap({super.key, required this.patient, this.ambulance, this.hospital, this.extraHospitals = const [],
      this.patientLabel = 'GPS'});
  static const _kigali = LatLng(-1.9441, 30.0619); // initial view only when nothing else is known

  Marker _m(LatLng p, String emoji) =>
      Marker(point: p, width: 36, height: 36, child: Text(emoji, style: const TextStyle(fontSize: 28)));

  @override
  Widget build(BuildContext context) {
    return FlutterMap(
      options: MapOptions(initialCenter: patient ?? ambulance ?? hospital ?? _kigali, initialZoom: patient != null ? 15 : 12),
      children: [
        // OpenStreetMap standard tiles (no API key). OSM's tile policy requires an identifying User-Agent, which
        // flutter_map builds from userAgentPackageName. (CARTO basemaps now return "API KEY REQUIRED" tiles.)
        TileLayer(
          urlTemplate: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
          userAgentPackageName: 'rw.ac.ur.smart_emergency',
          maxZoom: 19,
        ),
        PolylineLayer(polylines: [
          if (ambulance != null && patient != null) Polyline(points: [ambulance!, patient!], color: Colors.orange, strokeWidth: 4),
          if (hospital != null && patient != null) Polyline(points: [patient!, hospital!], color: Colors.blue, strokeWidth: 3),
        ]),
        MarkerLayer(markers: [
          for (final h in extraHospitals) _m(h, '🏥'),
          if (patient != null) _m(patient!, '📍'),
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
            child: Text(
                patient == null
                    ? '⚠ $patientLabel'
                    : '📍 $patientLabel: ${patient!.latitude.toStringAsFixed(5)}, ${patient!.longitude.toStringAsFixed(5)}',
                style: TextStyle(fontWeight: FontWeight.bold, color: patient == null ? Colors.red : null)),
          ),
        ),
        const RichAttributionWidget(attributions: [
          TextSourceAttribution('OpenStreetMap contributors'),
        ]),
      ],
    );
  }
}
