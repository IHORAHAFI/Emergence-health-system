import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'api_service.dart';

/// Accessibility + assistant settings. Kept on the phone (works offline, applies before login) and synced with
/// the server for patients (AssistantPreferences), so the backend and caregivers see the same choices.
class AppPrefs {
  double textScale = 1.0;
  bool highContrast = false;
  bool simplified = false;
  bool voiceAssistant = true;
  bool spokenResponses = true;
  bool aiVoiceCheckins = true;
  bool voiceNavigation = true;
  bool handsFree = false;
  bool fallDetection = false;
  bool checkinsEnabled = false;
  List<String> checkinTimes = [];
  bool notifyCaregivers = true;
  bool shareLocation = false;
  bool homeMonitoring = false;
  String language = 'en';
  double voiceRate = 0.45;

  static const serverKeys = {
    'text_scale', 'high_contrast', 'simplified', 'voice_assistant', 'spoken_responses', 'ai_voice_checkins',
    'voice_navigation', 'hands_free', 'fall_detection', 'checkins_enabled', 'checkin_times', 'notify_caregivers',
    'share_location_on_escalation', 'language', 'voice_rate',
  };

  Map<String, dynamic> toJson() => {
        'text_scale': textScale, 'high_contrast': highContrast, 'simplified': simplified,
        'voice_assistant': voiceAssistant, 'spoken_responses': spokenResponses, 'ai_voice_checkins': aiVoiceCheckins,
        'voice_navigation': voiceNavigation, 'hands_free': handsFree, 'fall_detection': fallDetection,
        'checkins_enabled': checkinsEnabled, 'checkin_times': checkinTimes, 'notify_caregivers': notifyCaregivers,
        'share_location_on_escalation': shareLocation, 'language': language, 'voice_rate': voiceRate,
        'home_monitoring': homeMonitoring,
      };

  void apply(Map<String, dynamic> j) {
    T v<T>(String k, T d) => j[k] is T ? j[k] as T : d;
    textScale = (j['text_scale'] as num?)?.toDouble() ?? textScale;
    highContrast = v('high_contrast', highContrast);
    simplified = v('simplified', simplified);
    voiceAssistant = v('voice_assistant', voiceAssistant);
    spokenResponses = v('spoken_responses', spokenResponses);
    aiVoiceCheckins = v('ai_voice_checkins', aiVoiceCheckins);
    voiceNavigation = v('voice_navigation', voiceNavigation);
    handsFree = v('hands_free', handsFree);
    fallDetection = v('fall_detection', fallDetection);
    checkinsEnabled = v('checkins_enabled', checkinsEnabled);
    checkinTimes = j['checkin_times'] is List ? List<String>.from(j['checkin_times']) : checkinTimes;
    notifyCaregivers = v('notify_caregivers', notifyCaregivers);
    shareLocation = v('share_location_on_escalation', shareLocation);
    homeMonitoring = v('home_monitoring', homeMonitoring);
    language = v('language', language);
    voiceRate = (j['voice_rate'] as num?)?.toDouble() ?? voiceRate;
  }
}

class PrefsService extends ValueNotifier<AppPrefs> {
  PrefsService._() : super(AppPrefs());
  static final instance = PrefsService._();

  AppPrefs get p => value;

  Future<void> load() async {
    try {
      final s = (await SharedPreferences.getInstance()).getString('app_prefs');
      if (s != null) value.apply(Map<String, dynamic>.from(jsonDecode(s)));
    } catch (_) {}
    notifyListeners();
  }

  Future<void> _saveLocal() async =>
      (await SharedPreferences.getInstance()).setString('app_prefs', jsonEncode(value.toJson()));

  /// Patients: fetch the server copy (source of truth for settings other people may change).
  Future<void> syncFromServer() async {
    try {
      value.apply(Map<String, dynamic>.from(await ApiService.instance.get('/assistant/preferences/')));
      await _saveLocal();
      notifyListeners();
    } catch (_) {/* offline or not a patient: keep local copy */}
  }

  /// Change settings locally at once, then save to the server (patients only; failures keep the local value).
  Future<String?> update(void Function(AppPrefs p) change, {bool patient = true}) async {
    change(value);
    notifyListeners();
    await _saveLocal();
    if (!patient) return null;
    try {
      final body = Map.fromEntries(value.toJson().entries.where((e) => AppPrefs.serverKeys.contains(e.key)));
      value.apply(Map<String, dynamic>.from(await ApiService.instance.patch('/assistant/preferences/', body)));
      await _saveLocal();
      notifyListeners();
      return null;
    } catch (e) {
      return '$e';
    }
  }

  Future<void> setHomeMonitoring(bool on) async {
    if (on) {
      await ApiService.instance.post('/home-monitoring/consent/');
    } else {
      await ApiService.instance.delete('/home-monitoring/consent/');
    }
    value.homeMonitoring = on;
    await _saveLocal();
    notifyListeners();
  }
}
