class Hospital {
  final int id;
  final String name;
  final String phone;
  final double latitude;
  final double longitude;
  final String levelDisplay;
  final int availableBeds;
  final int icuAvailable;
  final double? distanceKm;
  final List<String> specialties;

  Hospital({
    required this.id,
    required this.name,
    required this.phone,
    required this.latitude,
    required this.longitude,
    required this.levelDisplay,
    required this.availableBeds,
    required this.icuAvailable,
    this.distanceKm,
    this.specialties = const [],
  });

  factory Hospital.fromJson(Map<String, dynamic> j) => Hospital(
        id: j['id'],
        name: j['name'] ?? '',
        phone: j['phone'] ?? '',
        latitude: (j['latitude'] as num).toDouble(),
        longitude: (j['longitude'] as num).toDouble(),
        levelDisplay: j['level_display'] ?? '',
        availableBeds: j['available_beds'] ?? 0,
        icuAvailable: j['icu_available'] ?? 0,
        distanceKm: (j['distance_km'] as num?)?.toDouble(),
        specialties: ((j['specialties'] ?? []) as List).map((s) => s['name'].toString()).toList(),
      );
}
