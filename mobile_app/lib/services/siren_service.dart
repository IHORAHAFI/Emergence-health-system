import 'package:audioplayers/audioplayers.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'api_service.dart';

/// Plays the operator's selected siren (served by the backend, ALERT_SIREN_FILE) for unhandled requests.
///
/// The SERVER owns the schedule: every request has its own alert_count (1 on arrival, +1 every 2 minutes
/// while unaccepted). This class only remembers which count it already played per request, stored on the
/// phone, so restarting the app neither replays nor skips an alert, and accepting one request never
/// silences another. Only operator / admin accounts ever create this service.
class SirenService {
  SirenService._();
  static final instance = SirenService._();

  final _player = AudioPlayer();
  bool muted = false;
  String? _url;

  Future<void> load() async {
    final p = await SharedPreferences.getInstance();
    muted = p.getBool('siren_muted') ?? false;
  }

  Future<void> setMuted(bool v) async {
    muted = v;
    (await SharedPreferences.getInstance()).setBool('siren_muted', v);
    if (v) await _player.stop();
  }

  /// [sirenPath] is the feed's siren_url ("" when the file has not been installed on the server).
  /// Returns true when a siren was started.
  Future<bool> onFeed(List<Map<String, dynamic>> alerts, String sirenPath) async {
    final p = await SharedPreferences.getInstance();
    final played = Map<String, int>.from(
        {for (final k in p.getKeys().where((k) => k.startsWith('siren_played_'))) k.substring(14): p.getInt(k) ?? 0});
    final live = {for (final a in alerts) a['key'] as String};
    for (final k in played.keys.where((k) => !live.contains(k))) {
      await p.remove('siren_played_$k'); // handled: forget it
    }
    if (alerts.isEmpty) {
      await _player.stop();
      return false;
    }
    // at most 3 sirens per request (the server stops counting at 3; this guards older servers too)
    int count(Map a) => ((a['alert_count'] ?? 1) as int).clamp(1, 3);
    // ring only for an alert that happened just now: finished schedules (AI took over long ago) stay silent after a
    // fresh login; alert 3 and the takeover happen together, so a just-fired alert 3 still rings
    bool fresh(Map a) {
      final t = DateTime.tryParse('${a['last_alert_at'] ?? ''}');
      return t == null || DateTime.now().difference(t).inSeconds < 60;
    }
    final due = alerts.where((a) => (fresh(a) || a['ai_takeover'] != true) && count(a) > (played[a['key']] ?? 0)).toList();
    if (due.isEmpty) return false;
    for (final a in due) {
      await p.setInt('siren_played_${a['key']}', count(a));
    }
    if (muted || sirenPath.isEmpty) return false; // muted / no file: visual alert only
    _url = sirenPath.startsWith('http') ? sirenPath : '${ApiService.serverRoot}$sirenPath';
    try {
      await _player.stop();
      await _player.play(UrlSource(_url!), volume: 1.0);
      return true;
    } catch (_) {
      return false;
    }
  }

  Future<void> stop() => _player.stop();
}
