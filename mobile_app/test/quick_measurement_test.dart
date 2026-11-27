import 'dart:math';
import 'package:flutter/foundation.dart';

import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:smart_emergency/screens/heart_rate_screen.dart' show fingerCovers;
import 'package:smart_emergency/screens/temperature_screen.dart' show validateTemperature;
import 'package:smart_emergency/services/api_service.dart';
import 'package:smart_emergency/services/measurement_outbox.dart';
import 'package:smart_emergency/services/ppg.dart';
import 'package:smart_emergency/widgets/measure_widgets.dart';

/// Synthetic camera signal: a fingertip with a pulse of [bpm] (a dip per beat), slow drift, noise, 30 fps jitter.
PpgAnalyzer feed(double seconds, {double? bpm, double noise = 0.3, int seed = 1, bool finger = true}) {
  final rng = Random(seed);
  final a = PpgAnalyzer();
  var t = 0.0;
  while (t < seconds) {
    final pulse = bpm == null ? 0.0 : -2.0 * pow(sin(pi * t * bpm / 60), 8).toDouble(); // sharp systolic dip
    final v = 180 + 3 * sin(2 * pi * 0.1 * t) + pulse + noise * (rng.nextDouble() * 2 - 1);
    a.add(PpgFrame(v, finger, Duration(microseconds: (t * 1e6).round())));
    t += 1 / 30 + (rng.nextDouble() - 0.5) * 0.008;
  }
  return a;
}

void main() {
  group('PPG analyzer', () {
    for (final bpm in [48.0, 72.0, 95.0, 130.0, 170.0]) {
      test('estimates $bpm bpm from a clean signal', () {
        final r = feed(30, bpm: bpm).analyze();
        expect(r.verdict, PpgVerdict.valid, reason: r.reason);
        expect(r.bpm, closeTo(bpm, 3));
        expect(r.quality, greaterThan(0.45));
      });
    }

    test('needs at least 20 s of continuous signal', () {
      expect(feed(12, bpm: 72).analyze().verdict, PpgVerdict.needMoreData);
    });

    test('pure noise is never turned into a heart rate', () {
      for (var seed = 1; seed <= 20; seed++) {
        final r = feed(30, noise: 4, seed: seed).analyze();
        expect(r.isValid, isFalse, reason: 'seed $seed gave ${r.bpm}');
        expect(r.bpm, isNull);
      }
    });

    test('a flat signal (finger pressed too hard / no light) is inconclusive', () {
      final r = feed(30, noise: 0.0).analyze();
      expect(r.verdict, PpgVerdict.inconclusive);
    });

    test('a pulse buried in heavy noise is rejected rather than guessed', () {
      final r = feed(30, bpm: 72, noise: 12).analyze();
      expect(r.isValid, isFalse);
    });

    test('lifting the finger restarts the signal segment', () {
      final a = feed(25, bpm: 72);
      a.add(const PpgFrame(40, false, Duration(seconds: 26)));
      expect(a.signalSeconds, 0);
      expect(a.fingerLosses, 1);
      expect(a.analyze().verdict, PpgVerdict.needMoreData);
    });

    test('no finger at all gives no signal', () {
      final a = feed(40, bpm: 72, finger: false);
      expect(a.framesWithFinger, 0);
      expect(a.analyze().isValid, isFalse);
    });

    test('finger detection needs a red, lit image', () {
      expect(fingerCovers(120, 210), isTrue);  // flashlight through a fingertip
      expect(fingerCovers(120, 130), isFalse); // ordinary scene
      expect(fingerCovers(10, 200), isFalse);  // dark
    });
  });

  group('temperature validation', () {
    test('a body temperature is MEASURED with one decimal', () {
      final r = validateTemperature(37.26);
      expect(r.outcome, MeasureOutcome.measured);
      expect(r.value, 37.3);
    });
    test('implausible or missing values are INCONCLUSIVE, with no value', () {
      for (final v in [null, 22.0, 29.9, 43.1, 100.0]) {
        final r = validateTemperature(v);
        expect(r.outcome, MeasureOutcome.inconclusive);
        expect(r.valid, isFalse);
        expect(r.value, isNull);
      }
    });
  });

  group('measurement outbox', () {
    setUp(() => SharedPreferences.setMockInitialValues({}));
    Map<String, dynamic> payload() => {'client_id': MeasurementOutbox.newClientId(), 'kind': 'heart_rate'};
    Map<String, dynamic> ok({bool notified = true, bool duplicate = false}) =>
        {'saved': true, 'dashboard_notified': notified, 'duplicate': duplicate, 'measurement': {'id': 1}};

    Future<SendState> settle(ValueNotifier<SendState> s) async {
      for (var i = 0; i < 200 && (s.value.phase == SendPhase.sending || s.value.phase == SendPhase.retrying); i++) {
        await Future.delayed(const Duration(milliseconds: 5));
      }
      return s.value;
    }

    test('client ids are unique RFC 4122 v4 UUIDs', () {
      final ids = {for (var i = 0; i < 1000; i++) MeasurementOutbox.newClientId()};
      expect(ids.length, 1000);
      expect(ids.every((s) => RegExp(r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$').hasMatch(s)),
          isTrue);
    });

    test('sends automatically and reports sent only after the server confirms', () async {
      final sent = <Map>[];
      final box = MeasurementOutbox(sender: (b) async {
        sent.add(b);
        return ok();
      });
      final s = await box.submit(payload());
      final end = await settle(s);
      expect(end.phase, SendPhase.sent);
      expect(end.dashboardNotified, isTrue);
      expect(sent, hasLength(1));
      expect(await box.pending(), isEmpty);
    });

    test('network failures are retried with the SAME client id, then succeed', () async {
      final ids = <String>[];
      var calls = 0;
      final phases = <SendPhase>[];
      final box = MeasurementOutbox(baseBackoff: const Duration(milliseconds: 1), maxBackoff: const Duration(milliseconds: 2),
          sender: (b) async {
        ids.add(b['client_id']);
        if (++calls < 3) throw ApiException('Cannot reach the server.');
        return ok(duplicate: true); // the server had stored the first attempt already
      });
      final p = payload();
      final s = await box.submit(p);
      s.addListener(() => phases.add(s.value.phase));
      final end = await settle(s);
      expect(end.phase, SendPhase.sent);
      expect(ids.toSet(), {p['client_id']});
      expect(phases, contains(SendPhase.retrying));
      expect(await box.pending(), isEmpty);
    });

    test('kept on the phone while offline and re-sent by resume()', () async {
      var online = false;
      final box1 = MeasurementOutbox(baseBackoff: const Duration(seconds: 30), sender: (b) async {
        if (!online) throw ApiException('offline');
        return ok();
      });
      final p = payload();
      await box1.submit(p);
      await Future.delayed(const Duration(milliseconds: 20));
      expect((await box1.pending()).single['client_id'], p['client_id']);
      // app restarted, connection back
      online = true;
      final got = <String>[];
      final box2 = MeasurementOutbox(sender: (b) async {
        got.add(b['client_id']);
        return ok();
      });
      expect(await box2.resume(), 1);
      await settle(box2.state(p['client_id']));
      expect(got, [p['client_id']]);
      expect(await box2.pending(), isEmpty);
    });

    test('a server refusal (400) is reported and not retried', () async {
      var calls = 0;
      final box = MeasurementOutbox(sender: (b) async {
        calls++;
        throw ApiException('value: implausible', 400);
      });
      final end = await settle(await box.submit(payload()));
      expect(end.phase, SendPhase.rejected);
      expect(calls, 1);
      expect(await box.pending(), isEmpty);
    });

    test('a response without the saved confirmation is not reported as sent', () async {
      var calls = 0;
      final box = MeasurementOutbox(baseBackoff: const Duration(milliseconds: 1), maxBackoff: const Duration(milliseconds: 1),
          sender: (b) async => ++calls < 2 ? <String, dynamic>{} : ok());
      final end = await settle(await box.submit(payload()));
      expect(end.phase, SendPhase.sent);
      expect(calls, 2);
    });
  });
}
