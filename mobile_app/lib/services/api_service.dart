import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

import '../models/emergency.dart';
import '../models/hospital.dart';

class ApiException implements Exception {
  final String message;
  ApiException(this.message);
  @override
  String toString() => message;
}

class ApiService {
  ApiService._();
  static final instance = ApiService._();

  /// Android emulator reaches the host PC at 10.0.2.2.
  /// For a real phone use your PC's LAN IP, e.g. http://192.168.1.10:8000/api
  static const baseUrl = String.fromEnvironment('API_URL', defaultValue: 'http://10.0.2.2:8000/api');

  String? _token;
  bool get isLoggedIn => _token != null;

  Future<void> loadToken() async {
    final p = await SharedPreferences.getInstance();
    _token = p.getString('access');
  }

  Map<String, String> get _headers => {
        'Content-Type': 'application/json',
        if (_token != null) 'Authorization': 'Bearer $_token',
      };

  dynamic _handle(http.Response r) {
    final body = r.body.isEmpty ? null : jsonDecode(utf8.decode(r.bodyBytes));
    if (r.statusCode >= 200 && r.statusCode < 300) return body;
    if (r.statusCode == 401) logout();
    throw ApiException(body is Map ? body.values.map((v) => v.toString()).join('\n') : 'Error ${r.statusCode}');
  }

  Future<dynamic> get(String path, [Map<String, String>? q]) async =>
      _handle(await http.get(Uri.parse('$baseUrl$path').replace(queryParameters: q), headers: _headers));

  Future<dynamic> post(String path, Map<String, dynamic> data) async =>
      _handle(await http.post(Uri.parse('$baseUrl$path'), headers: _headers, body: jsonEncode(data)));

  // ---- auth ----
  Future<void> login(String username, String password) async {
    final data = await post('/auth/login/', {'username': username, 'password': password});
    _token = data['access'];
    final p = await SharedPreferences.getInstance();
    await p.setString('access', _token!);
  }

  Future<void> register(Map<String, dynamic> data) => post('/auth/register/', data);

  Future<Map<String, dynamic>> me() async => Map<String, dynamic>.from(await get('/auth/me/'));

  Future<void> logout() async {
    _token = null;
    final p = await SharedPreferences.getInstance();
    await p.remove('access');
  }

  // ---- hospitals ----
  Future<List<Hospital>> nearbyHospitals(double lat, double lng, {double radius = 30}) async {
    final list = await get('/hospitals/nearby/', {'lat': '$lat', 'lng': '$lng', 'radius': '$radius'});
    return (list as List).map((e) => Hospital.fromJson(e)).toList();
  }

  // ---- emergencies ----
  Future<Emergency> createEmergency(Map<String, dynamic> data) async =>
      Emergency.fromJson(await post('/emergencies/', data));

  Future<Emergency> getEmergency(int id) async => Emergency.fromJson(await get('/emergencies/$id/'));

  Future<List<Emergency>> myEmergencies({bool activeOnly = false}) async {
    final list = await get('/emergencies/', activeOnly ? {'active': '1'} : null);
    return (list as List).map((e) => Emergency.fromJson(e)).toList();
  }

  Future<Emergency> setStatus(int id, String status) async =>
      Emergency.fromJson(await post('/emergencies/$id/status/', {'status': status}));

  // ---- ambulance (paramedic) ----
  Future<Map<String, dynamic>> myAmbulance() async => Map<String, dynamic>.from(await get('/ambulances/mine/'));

  Future<void> updateAmbulanceLocation(double lat, double lng, {String? status}) =>
      post('/ambulances/mine/location/', {'latitude': lat, 'longitude': lng, if (status != null) 'status': status});

  Future<void> registerFcmToken(String token) => post('/auth/fcm-token/', {'token': token});
}
