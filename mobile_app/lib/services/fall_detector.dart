import 'dart:async';
import 'dart:math';

import 'package:flutter/foundation.dart';
import 'package:sensors_plus/sensors_plus.dart';

import 'api_service.dart';

/// POSSIBLE-fall detection with the phone's accelerometer, only while the app is running and only when the patient
/// switched it on. Pattern: short free fall (< 0.3 g) -> hard impact (> 2.5 g) within 1 s -> phone lies still.
/// A phone is not a medical fall detector: this only starts a check-in ("Are you okay?"); it never claims a fall
/// happened, and a phone dropped on a table can trigger it (the patient just answers "I'm okay").
class FallDetector {
  FallDetector._();
  static final instance = FallDetector._();

  StreamSubscription<AccelerometerEvent>? _sub;
  DateTime? _freeFallAt, _impactAt, _lastReport;
  final _afterImpact = <double>[];
  static const g = 9.81;

  bool get running => _sub != null;

  void start() {
    if (_sub != null || kIsWeb) return;
    try {
      _sub = accelerometerEventStream(samplingPeriod: SensorInterval.gameInterval).listen(_on, onError: (_) => stop());
    } catch (_) {
      _sub = null;
    }
  }

  void stop() {
    _sub?.cancel();
    _sub = null;
  }

  @visibleForTesting
  bool feed(double magnitude, DateTime now) {
    if (magnitude < 0.3 * g) {
      _freeFallAt = now;
    } else if (magnitude > 2.5 * g && _freeFallAt != null && now.difference(_freeFallAt!).inMilliseconds < 1000) {
      _impactAt = now;
      _afterImpact.clear();
      _freeFallAt = null;
    } else if (_impactAt != null) {
      _afterImpact.add(magnitude);
      if (now.difference(_impactAt!).inMilliseconds > 2500) {
        final still = _afterImpact.isNotEmpty &&
            _afterImpact.map((m) => (m - g).abs()).reduce(max) < 0.25 * g; // lying still after the impact
        _impactAt = null;
        if (still && (_lastReport == null || now.difference(_lastReport!).inSeconds > 60)) {
          _lastReport = now;
          return true;
        }
      }
    }
    return false;
  }

  void _on(AccelerometerEvent e) {
    if (feed(sqrt(e.x * e.x + e.y * e.y + e.z * e.z), DateTime.now())) {
      ApiService.instance.post('/monitoring/events/', {'kind': 'possible_fall'}).catchError((_) => null);
    }
  }
}
