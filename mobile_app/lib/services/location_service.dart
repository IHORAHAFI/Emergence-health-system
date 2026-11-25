import 'package:geolocator/geolocator.dart';

class LocationService {
  /// Returns current high-accuracy GPS position, asking for permission if needed.
  static Future<Position> current() async {
    if (!await Geolocator.isLocationServiceEnabled()) {
      throw Exception('Location services are disabled. Please turn on GPS.');
    }
    var perm = await Geolocator.checkPermission();
    if (perm == LocationPermission.denied) perm = await Geolocator.requestPermission();
    if (perm == LocationPermission.denied || perm == LocationPermission.deniedForever) {
      throw Exception('Location permission denied.');
    }
    return Geolocator.getCurrentPosition(desiredAccuracy: LocationAccuracy.high);
  }

  static Stream<Position> stream() => Geolocator.getPositionStream(
        locationSettings: const LocationSettings(accuracy: LocationAccuracy.high, distanceFilter: 20),
      );
}
