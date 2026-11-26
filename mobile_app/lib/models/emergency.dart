import 'hospital.dart';

class Emergency {
  final int id;
  final String reference;
  final DateTime? createdAt;
  final String type;
  final String typeDisplay;
  final String status;
  final String statusDisplay;
  final String priority;
  /// null when no location was shared (the server never sends placeholder coordinates)
  final double? latitude;
  final double? longitude;
  final double? etaMinutes;
  final Hospital? hospital;
  final String? ambulancePlate;
  final double? ambulanceLat;
  final double? ambulanceLng;
  final String patientName;
  final String patientPhone;
  final List<Map<String, dynamic>> logs;
  /// Full server record (symptoms, escalation summary, ...) for the staff screens.
  final Map<String, dynamic> raw;
  String get operatorLabel => (raw['operator_label'] ?? statusDisplay).toString();
  bool get accepted => raw['accepted_at'] != null;
  String? get acceptedBy => raw['accepted_by_name'];
  Map<String, dynamic> get escalation => Map<String, dynamic>.from(raw['escalation'] ?? const {});
  bool get escalated => raw['escalated_at'] != null;
  /// live / last_known / manual / simulated / unavailable (computed by the server)
  Map<String, dynamic> get location => Map<String, dynamic>.from(raw['location_status'] ?? const {});
  String get locationState => (location['state'] ?? (latitude == null ? 'unavailable' : 'manual')).toString();
  String get locationLabel => (location['label'] ?? (latitude == null ? 'LOCATION UNAVAILABLE' : 'LOCATION')).toString();

  Emergency({
    required this.id,
    this.reference = '',
    this.createdAt,
    required this.type,
    required this.typeDisplay,
    required this.status,
    required this.statusDisplay,
    required this.priority,
    this.latitude,
    this.longitude,
    this.etaMinutes,
    this.hospital,
    this.ambulancePlate,
    this.ambulanceLat,
    this.ambulanceLng,
    this.patientName = '',
    this.patientPhone = '',
    this.logs = const [],
    this.raw = const {},
  });

  bool get isActive => status != 'completed' && status != 'cancelled';

  factory Emergency.fromJson(Map<String, dynamic> j) {
    final amb = j['assigned_ambulance'];
    final hosp = j['assigned_hospital'];
    return Emergency(
      id: j['id'],
      reference: j['reference'] ?? '',
      createdAt: DateTime.tryParse(j['created_at'] ?? '')?.toLocal(),
      type: j['emergency_type'],
      typeDisplay: j['type_display'] ?? j['emergency_type'],
      status: j['status'],
      statusDisplay: j['status_display'] ?? j['status'],
      priority: j['priority'] ?? 'medium',
      latitude: (j['latitude'] as num?)?.toDouble(),
      longitude: (j['longitude'] as num?)?.toDouble(),
      etaMinutes: (j['eta_minutes'] as num?)?.toDouble(),
      hospital: hosp is Map<String, dynamic> ? Hospital.fromJson(hosp) : null,
      ambulancePlate: amb is Map ? amb['plate_number'] : null,
      ambulanceLat: amb is Map ? (amb['latitude'] as num).toDouble() : null,
      ambulanceLng: amb is Map ? (amb['longitude'] as num).toDouble() : null,
      patientName: j['patient_name'] ?? '',
      patientPhone: j['patient_phone'] ?? '',
      logs: ((j['logs'] ?? []) as List).map((e) => Map<String, dynamic>.from(e)).toList(),
      raw: j,
    );
  }
}
