import 'dart:async';
import 'dart:convert';
import 'dart:math';

import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'api_service.dart';

/// Automatic, reliable upload of finished measurements to Django (POST /api/measurements/).
///
/// * Each measurement gets a random UUID ([newClientId]) BEFORE the first attempt; the server stores a client_id
///   once, so a retry after a lost response never creates a duplicate.
/// * The payload is written to the phone (SharedPreferences) before sending and removed only when Django
///   confirms it was saved, so no connection drop or app restart loses a measurement. [resume] re-sends anything
///   left over (called at start-up).
/// * Network errors, timeouts, 5xx, 408, 429 and an expired session are retried with backoff (2 s .. 15 s);
///   any other 4xx means the server refused the data: it is reported and not retried.
enum SendPhase { sending, retrying, sent, rejected }

class SendState {
  final SendPhase phase;
  final int attempt;
  final String? error;
  final bool dashboardNotified; // the server's real-time push to operator dashboards was accepted
  final bool duplicate;
  final Map<String, dynamic>? measurement;
  const SendState(this.phase, {this.attempt = 1, this.error, this.dashboardNotified = false, this.duplicate = false,
      this.measurement});
}

typedef MeasurementSender = Future<dynamic> Function(Map<String, dynamic> body);

class MeasurementOutbox {
  MeasurementOutbox({MeasurementSender? sender, this.maxBackoff = const Duration(seconds: 15),
      this.baseBackoff = const Duration(seconds: 2)})
      : _sender = sender ?? ((b) => ApiService.instance.post('/measurements/', b));
  static final instance = MeasurementOutbox();
  static const _key = 'measurement_outbox_v1';

  final MeasurementSender _sender;
  final Duration maxBackoff, baseBackoff;
  final _states = <String, ValueNotifier<SendState>>{};
  final _inFlight = <String>{};

  static String newClientId() {
    final r = Random.secure();
    final b = List<int>.generate(16, (_) => r.nextInt(256));
    b[6] = (b[6] & 0x0f) | 0x40; // version 4
    b[8] = (b[8] & 0x3f) | 0x80; // RFC 4122 variant
    final h = b.map((x) => x.toRadixString(16).padLeft(2, '0')).join();
    return '${h.substring(0, 8)}-${h.substring(8, 12)}-${h.substring(12, 16)}-${h.substring(16, 20)}-${h.substring(20)}';
  }

  ValueNotifier<SendState> state(String clientId) =>
      _states.putIfAbsent(clientId, () => ValueNotifier(const SendState(SendPhase.sending)));

  /// Stores the measurement on the phone, then sends it (retrying) in the background.
  /// Returns the notifier the UI listens to.
  Future<ValueNotifier<SendState>> submit(Map<String, dynamic> payload) async {
    final id = payload['client_id'] as String;
    await _save(id, payload);
    final s = state(id);
    unawaited(_deliver(id, payload));
    return s;
  }

  /// Re-sends measurements that were stored but never confirmed (e.g. the app was closed while offline).
  Future<int> resume() async {
    final pending = await _load();
    for (final e in pending.entries) {
      unawaited(_deliver(e.key, e.value));
    }
    return pending.length;
  }

  Future<List<Map<String, dynamic>>> pending() async => (await _load()).values.toList();

  Future<void> _deliver(String id, Map<String, dynamic> payload) async {
    if (!_inFlight.add(id)) return; // already being sent (submit + resume at the same moment)
    final s = state(id);
    try {
      for (var attempt = 1;; attempt++) {
        s.value = SendState(attempt == 1 ? SendPhase.sending : SendPhase.retrying, attempt: attempt,
            error: s.value.error);
        try {
          final r = await _sender(payload);
          final body = r is Map ? Map<String, dynamic>.from(r) : <String, dynamic>{};
          if (body['saved'] != true) throw ApiException('The server did not confirm the measurement.', 500);
          await _remove(id);
          s.value = SendState(SendPhase.sent, attempt: attempt, dashboardNotified: body['dashboard_notified'] == true,
              duplicate: body['duplicate'] == true,
              measurement: body['measurement'] is Map ? Map<String, dynamic>.from(body['measurement']) : null);
          return;
        } catch (e) {
          final code = e is ApiException ? e.status : 0;
          final retryable = code == 0 || code == 401 || code == 408 || code == 429 || code >= 500;
          if (!retryable) {
            await _remove(id);
            s.value = SendState(SendPhase.rejected, attempt: attempt, error: '$e');
            return;
          }
          s.value = SendState(SendPhase.retrying, attempt: attempt, error: '$e');
          final wait = baseBackoff * pow(2, min(attempt - 1, 6)).toInt();
          await Future.delayed(wait > maxBackoff ? maxBackoff : wait);
        }
      }
    } finally {
      _inFlight.remove(id);
    }
  }

  Future<Map<String, Map<String, dynamic>>> _load() async {
    final raw = (await SharedPreferences.getInstance()).getString(_key);
    if (raw == null) return {};
    try {
      return (jsonDecode(raw) as Map).map((k, v) => MapEntry(k as String, Map<String, dynamic>.from(v)));
    } catch (_) {
      return {};
    }
  }

  Future<void> _write(Map<String, Map<String, dynamic>> m) async =>
      (await SharedPreferences.getInstance()).setString(_key, jsonEncode(m));

  Future<void> _save(String id, Map<String, dynamic> p) async => _write({...await _load(), id: p});

  Future<void> _remove(String id) async {
    final m = await _load();
    if (m.remove(id) != null) await _write(m);
  }
}
