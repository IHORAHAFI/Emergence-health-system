import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

import '../models/emergency.dart';
import '../models/hospital.dart';
import '../models/test_request.dart';
import '../models/vital_reading.dart';

class ApiException implements Exception {
  final String message;
  final int status;
  ApiException(this.message, [this.status = 0]);
  @override
  String toString() => message;
}

class ApiService {
  ApiService._();
  static final instance = ApiService._();

  /// Set at build time:  flutter run --dart-define=API_URL=https://your-app.onrender.com/api
  /// Android emulator reaches the host PC at 10.0.2.2; a real phone needs your PC's LAN IP.
  static const baseUrl = String.fromEnvironment('API_URL', defaultValue: 'http://10.0.2.2:8000/api');

  String? _token;
  String? _refresh;
  bool get isLoggedIn => _token != null;

  /// Called when the session expires so the UI can return to the login screen.
  void Function()? onSessionExpired;

  Future<void> loadToken() async {
    final p = await SharedPreferences.getInstance();
    _token = p.getString('access');
    _refresh = p.getString('refresh');
  }

  Map<String, String> get _headers => {
        'Content-Type': 'application/json',
        'Accept-Language': 'en',
        if (_token != null) 'Authorization': 'Bearer $_token',
      };

  String _message(dynamic body, int code) {
    if (body is Map) {
      if (body['detail'] != null) return body['detail'].toString();
      return body.entries
          .map((e) => '${e.key == 'non_field_errors' ? '' : '${e.key}: '}${e.value is List ? (e.value as List).join(' ') : e.value}')
          .join('\n');
    }
    if (body is List) return body.join('\n');
    return code == 0 ? 'Cannot reach the server' : 'Server error ($code)';
  }

  Future<bool> _tryRefresh() async {
    if (_refresh == null) return false;
    try {
      final r = await http.post(Uri.parse('$baseUrl/auth/refresh/'),
          headers: {'Content-Type': 'application/json'}, body: jsonEncode({'refresh': _refresh}));
      if (r.statusCode != 200) return false;
      _token = jsonDecode(r.body)['access'];
      (await SharedPreferences.getInstance()).setString('access', _token!);
      return true;
    } catch (_) {
      return false;
    }
  }

  Future<dynamic> _send(Future<http.Response> Function() call, {bool retry = true}) async {
    http.Response r;
    try {
      r = await call().timeout(const Duration(seconds: 30));
    } catch (_) {
      throw ApiException('Cannot reach the server. Check your internet connection.');
    }
    if (r.statusCode == 401 && retry && _token != null && await _tryRefresh()) {
      return _send(call, retry: false);
    }
    dynamic body;
    try {
      body = r.body.isEmpty ? null : jsonDecode(utf8.decode(r.bodyBytes));
    } catch (_) {
      body = null;
    }
    if (r.statusCode >= 200 && r.statusCode < 300) return body;
    if (r.statusCode == 401 && _token != null) {
      await logout();
      onSessionExpired?.call();
    }
    throw ApiException(_message(body, r.statusCode), r.statusCode);
  }

  Uri _u(String path, [Map<String, String>? q]) => Uri.parse('$baseUrl$path').replace(queryParameters: q);

  Future<dynamic> get(String path, [Map<String, String>? q]) => _send(() => http.get(_u(path, q), headers: _headers));
  Future<dynamic> post(String path, [Map<String, dynamic>? data]) =>
      _send(() => http.post(_u(path), headers: _headers, body: jsonEncode(data ?? {})));
  Future<dynamic> patch(String path, Map<String, dynamic> data) =>
      _send(() => http.patch(_u(path), headers: _headers, body: jsonEncode(data)));
  Future<dynamic> delete(String path) => _send(() => http.delete(_u(path), headers: _headers));

  // ---- auth ----
  Future<void> login(String username, String password) async {
    final data = await _send(
        () => http.post(_u('/auth/login/'), headers: {'Content-Type': 'application/json'},
            body: jsonEncode({'username': username, 'password': password})),
        retry: false);
    _token = data['access'];
    _refresh = data['refresh'];
    final p = await SharedPreferences.getInstance();
    await p.setString('access', _token!);
    if (_refresh != null) await p.setString('refresh', _refresh!);
  }

  Future<void> register(Map<String, dynamic> data) => post('/auth/register/', data);

  Future<Map<String, dynamic>> me() async => Map<String, dynamic>.from(await get('/auth/me/'));
  Future<Map<String, dynamic>> updateMe(Map<String, dynamic> d) async => Map<String, dynamic>.from(await patch('/auth/me/', d));
  Future<Map<String, dynamic>> profile() async => Map<String, dynamic>.from(await get('/auth/profile/'));
  Future<Map<String, dynamic>> updateProfile(Map<String, dynamic> d) async =>
      Map<String, dynamic>.from(await patch('/auth/profile/', d));

  Future<void> logout() async {
    _token = null;
    _refresh = null;
    final p = await SharedPreferences.getInstance();
    await p.remove('access');
    await p.remove('refresh');
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

  /// Consent to share vital signs with the team handling this emergency.
  Future<void> giveMonitoringConsent(String ref) => post('/monitoring/$ref/consent/');

  /// A REAL Bluetooth measurement sent during an emergency (requires consent).
  Future<VitalReading> sendEmergencyReading(String ref, Map<String, dynamic> values, Map<String, dynamic> device) async =>
      VitalReading.fromJson(await post('/monitoring/$ref/readings/', {...values, 'source': 'bluetooth', 'device': device}));

  // ---- medical tests ----
  Future<List<TestRequest>> myTests({bool openOnly = false}) async {
    final list = await get('/tests/', openOnly ? {'open': '1'} : null);
    return (list as List).map((e) => TestRequest.fromJson(e)).toList();
  }

  Future<TestRequest> requestTests(List<String> codes, {String note = ''}) async =>
      TestRequest.fromJson(await post('/tests/', {'tests': codes, 'note': note}));

  Future<TestRequest> getTest(String ref) async => TestRequest.fromJson(await get('/tests/$ref/'));
  Future<void> cancelTest(String ref) => post('/tests/$ref/cancel/');

  /// A REAL Bluetooth measurement for a running test.
  Future<void> sendTestReading(String ref, Map<String, dynamic> values, Map<String, dynamic> device) =>
      post('/tests/$ref/readings/', {...values, 'device': device});

  Future<List<VitalReading>> latestVitals({int limit = 30}) async {
    final list = await get('/vitals/latest/', {'limit': '$limit'});
    return (list as List).map((e) => VitalReading.fromJson(e)).toList();
  }

  /// Arduino / USB-serial sensor readings from devices assigned to me (server enforces: mine only).
  /// Vital Signs section: own camera heart rate (CAMERA_MEASURED_ESTIMATE) + simulated temperature / blood pressure.
  Future<Map<String, dynamic>> myVitals({int limit = 30}) async =>
      Map<String, dynamic>.from(await get('/vitals/mine/', {'limit': '$limit'}));
  Future<Map<String, dynamic>> myIotReadings() async => Map<String, dynamic>.from(await get('/iot/my-readings/'));

  // ---- notifications ----
  Future<List<Map<String, dynamic>>> notifications({bool unreadOnly = false}) async {
    final list = await get('/notifications/', unreadOnly ? {'unread': '1'} : null);
    return (list as List).map((e) => Map<String, dynamic>.from(e)).toList();
  }

  Future<void> markRead(int id) => post('/notifications/$id/read/');
  Future<void> markAllRead() => post('/notifications/read-all/');

  // ---- medical information ----
  Future<List<Map<String, dynamic>>> diseases({String q = ''}) async {
    final list = await get('/diseases/', q.isEmpty ? null : {'q': q});
    return (list as List).map((e) => Map<String, dynamic>.from(e)).toList();
  }

  Future<Map<String, dynamic>> disease(String slug) async => Map<String, dynamic>.from(await get('/diseases/$slug/'));

  Future<Map<String, dynamic>> ask(String question,
          {int? conversationId, bool saveHistory = true, bool includeVitals = false, String? emergencyRef}) async =>
      Map<String, dynamic>.from(await post('/assistant/ask/', {
        'question': question,
        if (conversationId != null) 'conversation_id': conversationId,
        'save_history': saveHistory,
        'include_vitals': includeVitals,
        if (emergencyRef != null) 'emergency_ref': emergencyRef,
      }));

  /// Rate one answer (helpful / unhelpful / unclear) or record that a suggestion was used. Labels only.
  Future<void> assistantFeedback(Map<String, dynamic> answer, String rating, {String? suggestion}) =>
      post('/assistant/feedback/', {
        'response_id': answer['response_id'],
        if (answer['message_id'] != null) 'message_id': answer['message_id'],
        'rating': rating,
        if (suggestion != null) 'suggestion': suggestion,
      });

  Future<List<Map<String, dynamic>>> assistantHistory() async =>
      List<Map<String, dynamic>>.from(((await get('/assistant/history/'))['conversations'] as List)
          .map((e) => Map<String, dynamic>.from(e)));
  Future<Map<String, dynamic>> assistantConversation(int id) async =>
      Map<String, dynamic>.from(await get('/assistant/history/$id/'));
  Future<void> deleteAssistantHistory([int? id]) => delete(id == null ? '/assistant/history/' : '/assistant/history/$id/');

  // ---- password ----
  Future<String> requestPasswordReset(String email) async =>
      (await post('/auth/password-reset/', {'email': email}))['detail'] ?? '';
  Future<String> confirmPasswordReset(String uid, String token, String newPassword) async =>
      (await post('/auth/password-reset/confirm/', {'uid': uid, 'token': token, 'new_password': newPassword}))['detail'] ?? '';
  Future<String> changePassword(String oldPassword, String newPassword) async =>
      (await post('/auth/password-change/', {'old_password': oldPassword, 'new_password': newPassword}))['detail'] ?? '';

  // ---- operator / medical staff ----
  /// Server root (for static files such as the siren), e.g. https://host
  static String get serverRoot => baseUrl.replaceFirst(RegExp(r'/api/?$'), '');
  Future<Map<String, dynamic>> staffAlerts() async => Map<String, dynamic>.from(await get('/staff/alerts/'));
  Future<Map<String, dynamic>> acceptAlert(String key) async =>
      Map<String, dynamic>.from(await post('/staff/alerts/accept/', {'key': key}));
  Future<Emergency> acceptEmergency(int id) async => Emergency.fromJson(await post('/emergencies/$id/accept/'));
  Future<Emergency> updateEmergencyStatus(int id, String status, {String note = ''}) async =>
      Emergency.fromJson(await post('/emergencies/$id/status/', {'status': status, 'note': note}));
  Future<void> sendEmergencyMessage(int id, String text) => post('/emergencies/$id/message/', {'text': text});

  /// Live GPS from the patient's phone for their own open emergency (server validates and throttles).
  Future<Map<String, dynamic>> shareLocation(int id, double lat, double lng, {double? accuracy, DateTime? at}) async =>
      Map<String, dynamic>.from(await post('/emergencies/$id/location/', {
        'latitude': lat, 'longitude': lng, if (accuracy != null) 'accuracy': accuracy,
        'timestamp': (at ?? DateTime.now()).toUtc().toIso8601String(), 'source': 'gps',
      }));
  Future<List<VitalReading>> emergencyVitals(String ref) async =>
      ((await get('/monitoring/$ref/readings/')) as List).map((e) => VitalReading.fromJson(e)).toList();
  Future<List<Map<String, dynamic>>> staffTests({bool openOnly = true}) async =>
      ((await get('/staff/tests/', openOnly ? {'open': '1'} : null)) as List).map((e) => Map<String, dynamic>.from(e)).toList();
  Future<Map<String, dynamic>> testAction(String ref, String action, {String scenario = 'normal'}) async =>
      Map<String, dynamic>.from(await post('/staff/tests/$ref/action/', {'action': action, 'scenario': scenario}));
  Future<Map<String, dynamic>> staffStats() async => Map<String, dynamic>.from(await get('/staff/stats/'));

  // ---- ambulance (paramedic) ----
  Future<Map<String, dynamic>> myAmbulance() async => Map<String, dynamic>.from(await get('/ambulances/mine/'));

  Future<void> updateAmbulanceLocation(double lat, double lng, {String? status}) =>
      post('/ambulances/mine/location/', {'latitude': lat, 'longitude': lng, if (status != null) 'status': status});

  Future<void> registerFcmToken(String token) => post('/auth/fcm-token/', {'token': token});
}
