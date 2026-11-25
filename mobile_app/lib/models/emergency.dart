import 'hospital.dart';

class Emergency {
  final int id;
  final String type;
  final String typeDisplay;
  final String status;
  final String statusDisplay;
  final String priority;
  final double latitude;
  final double longitude;
  final double? etaMinutes;
  final Hospital? hospital;
  final String? ambulancePlate;
  final double? ambulanceLat;
  final double? ambulanceLng;
  final String patientName;
  final String patientPhone;
  final List<Map<String, dynamic>> logs;

  Emergency({
    required this.id,
    required this.type,
    required this.typeDisplay,
    required this.status,
    required this.statusDisplay,
    required this.priority,
    required this.latitude,
    required this.longitude,
    this.etaMinutes,
    this.hospital,
    this.ambulancePlate,
    this.ambulanceLat,
    this.ambulanceLng,
    this.patientName = '',
    this.patientPhone = '',
    this.logs = const [],
  });

  bool get isActive => status != 'completed' && status != 'cancelled';

  factory Emergency.fromJson(Map<String, dynamic> j) {
    final amb = j['assigned_ambulance'];
    final hosp = j['assigned_hospital'];
    return Emergency(
      id: j['id'],
      type: j['emergency_type'],
      typeDisplay: j['type_display'] ?? j['emergency_type'],
      status: j['status'],
      statusDisplay: j['status_display'] ?? j['status'],
      priority: j['priority'] ?? 'medium',
      latitude: (j['latitude'] as num).toDouble(),
      longitude: (j['longitude'] as num).toDouble(),
      etaMinutes: (j['eta_minutes'] as num?)?.toDouble(),
      hospital: hosp is Map<String, dynamic> ? Hospital.fromJson(hosp) : null,
      ambulancePlate: amb is Map ? amb['plate_number'] : null,
      ambulanceLat: amb is Map ? (amb['latitude'] as num).toDouble() : null,
      ambulanceLng: amb is Map ? (amb['longitude'] as num).toDouble() : null,
      patientName: j['patient_name'] ?? '',
      patientPhone: j['patient_phone'] ?? '',
      logs: ((j['logs'] ?? []) as List).map((e) => Map<String, dynamic>.from(e)).toList(),
    );
  }
}
