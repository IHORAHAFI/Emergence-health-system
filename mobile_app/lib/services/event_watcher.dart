import 'dart:async';

import 'package:flutter/widgets.dart';

import 'api_service.dart';
import 'local_notify.dart';
import 'prefs_service.dart';

/// Watches for monitoring events where the AI is waiting for the patient (concerning readings, check-ins,
/// possible falls). The SERVER decides when to ask, repeats the question and escalates on no answer - this class
/// only brings the question to the patient:
///   * app on screen and AI voice check-ins on -> opens the spoken check-in screen at once (onAsk);
///   * app in the background -> a high-priority local notification; tapping it opens the check-in.
/// Polling is used because push (Firebase) is not configured; it stops when the OS suspends the app.
class EventWatcher with WidgetsBindingObserver {
  EventWatcher._();
  static final instance = EventWatcher._();

  Timer? _timer;
  bool _foreground = true;
  final _handled = <String>{}; // "id:prompt_count" already shown, so a poll never repeats the same prompt
  void Function(Map<String, dynamic> event)? onAsk;

  void start() {
    stop();
    WidgetsBinding.instance.addObserver(this);
    _check();
    _timer = Timer.periodic(const Duration(seconds: 15), (_) => _check());
  }

  void stop() {
    _timer?.cancel();
    _timer = null;
    WidgetsBinding.instance.removeObserver(this);
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    _foreground = state == AppLifecycleState.resumed;
    if (_foreground) _check();
  }

  Future<void> _check() async {
    List events;
    try {
      events = await ApiService.instance.get('/monitoring/events/', {'open': '1'}) as List;
    } catch (_) {
      return; // offline: the server keeps counting and will escalate if needed
    }
    for (final raw in events) {
      final e = Map<String, dynamic>.from(raw);
      if (e['status'] != 'awaiting_response') continue;
      final key = '${e['id']}:${e['prompt_count']}';
      if (_handled.contains(key)) continue;
      _handled.add(key);
      if (_foreground && PrefsService.instance.p.aiVoiceCheckins && onAsk != null) {
        onAsk!(e);
      } else {
        await LocalNotify.instance.show(e['id'] as int, 'Your medical assistant is checking on you',
            e['prompt'] ?? 'Are you feeling okay?', payload: 'event:${e['id']}');
      }
    }
  }
}
