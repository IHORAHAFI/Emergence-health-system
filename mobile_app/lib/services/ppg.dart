/// Camera photoplethysmography (PPG): estimate the pulse from the brightness of a fingertip lit by the flashlight.
///
/// Pure Dart (no camera code) so it can be unit-tested with synthetic signals. Every frame gives ONE number (mean
/// red / luma of the centre of the image) plus a "finger present" flag; [PpgAnalyzer] keeps the latest continuous
/// finger-on segment and estimates the rate with two independent methods:
///   1. autocorrelation of the detrended signal (dominant period, 40-200 bpm),
///   2. peak detection (median beat-to-beat interval).
/// A result is returned ONLY when both agree and the signal is periodic enough. Otherwise the analyzer says why it
/// is inconclusive - it never fills in a plausible number.
library;

import 'dart:math' as math;

class PpgFrame {
  final double value; // mean brightness of the fingertip region (0..255)
  final bool fingerPresent;
  final Duration t; // time since the measurement started
  const PpgFrame(this.value, this.fingerPresent, this.t);
}

enum PpgVerdict { valid, needMoreData, inconclusive }

class PpgResult {
  final PpgVerdict verdict;
  final int? bpm;
  final double quality; // 0..1 (autocorrelation peak)
  final String reason;
  final Map<String, dynamic> details;
  const PpgResult(this.verdict, {this.bpm, this.quality = 0, this.reason = '', this.details = const {}});
  bool get isValid => verdict == PpgVerdict.valid && bpm != null;
}

class PpgAnalyzer {
  /// Minimum continuous finger-on signal before a result is attempted.
  final Duration minSignal;
  /// Signal kept for analysis (older samples are dropped).
  final Duration window;
  final double minQuality;
  final double maxDisagreement; // relative difference allowed between the two methods
  PpgAnalyzer({
    this.minSignal = const Duration(seconds: 20),
    this.window = const Duration(seconds: 30),
    this.minQuality = 0.45,
    this.maxDisagreement = 0.12,
  });

  static const sampleHz = 30.0;
  static const minBpm = 40, maxBpm = 200;

  final _t = <double>[]; // seconds
  final _v = <double>[];
  int framesTotal = 0, framesWithFinger = 0, fingerLosses = 0;
  bool _lastFinger = false;

  /// Seconds of continuous finger-on signal collected so far.
  double get signalSeconds => _t.length < 2 ? 0 : _t.last - _t.first;
  bool get fingerPresent => _lastFinger;

  void add(PpgFrame f) {
    framesTotal++;
    if (!f.fingerPresent) {
      if (_lastFinger) fingerLosses++;
      _lastFinger = false;
      _t.clear(); // a lifted finger breaks the signal: start the segment again
      _v.clear();
      return;
    }
    _lastFinger = true;
    framesWithFinger++;
    final s = f.t.inMicroseconds / 1e6;
    if (_t.isNotEmpty && s <= _t.last) return; // out-of-order frame
    _t.add(s);
    _v.add(f.value);
    final keepFrom = s - window.inMicroseconds / 1e6;
    while (_t.isNotEmpty && _t.first < keepFrom) {
      _t.removeAt(0);
      _v.removeAt(0);
    }
  }

  PpgResult analyze() {
    final base = {'signal_s': double.parse(signalSeconds.toStringAsFixed(1)), 'frames': framesTotal,
        'finger_frames': framesWithFinger, 'finger_losses': fingerLosses};
    if (signalSeconds < minSignal.inMicroseconds / 1e6) {
      return PpgResult(PpgVerdict.needMoreData, reason: 'not enough continuous signal', details: base);
    }
    final x = _resample();
    if (x.length < sampleHz * 8) {
      return PpgResult(PpgVerdict.needMoreData, reason: 'too few camera frames', details: base);
    }
    final y = _detrend(x);
    final sd = _std(y);
    if (sd < 0.05) {
      return PpgResult(PpgVerdict.inconclusive, reason: 'no pulse visible in the camera signal (flat signal)',
          details: {...base, 'amplitude': sd});
    }
    // 1. autocorrelation
    final minLag = (sampleHz * 60 / maxBpm).floor(), maxLag = (sampleHz * 60 / minBpm).ceil();
    final ac = <double>[];
    final energy = y.fold<double>(0, (a, b) => a + b * b);
    for (var lag = 0; lag <= maxLag && lag < y.length; lag++) {
      var s = 0.0;
      for (var i = 0; i + lag < y.length; i++) {
        s += y[i] * y[i + lag];
      }
      ac.add(s / energy * y.length / (y.length - lag));
    }
    var bestLag = -1;
    var best = -1.0;
    for (var lag = minLag + 1; lag < ac.length - 1; lag++) {
      if (ac[lag] > ac[lag - 1] && ac[lag] >= ac[lag + 1] && ac[lag] > best) {
        best = ac[lag];
        bestLag = lag;
      }
    }
    if (bestLag < 0) {
      return PpgResult(PpgVerdict.inconclusive, reason: 'no regular rhythm found', details: base);
    }
    // Octave check: at lag 2T, 3T... the signal also correlates with itself, sometimes slightly better than at the
    // true period T. Reporting 2T would HALVE the heart rate, so take the shortest lag whose peak is close to best.
    for (var l = minLag + 1; l < bestLag; l++) {
      if (ac[l] > ac[l - 1] && ac[l] >= ac[l + 1] && ac[l] >= 0.8 * best) {
        bestLag = l;
        best = ac[l];
        break;
      }
    }
    // parabolic interpolation around the peak for sub-sample accuracy
    final a = ac[bestLag - 1], b = ac[bestLag], c = ac[bestLag + 1];
    final denom = a - 2 * b + c;
    final lag = bestLag + (denom.abs() > 1e-9 ? 0.5 * (a - c) / denom : 0.0);
    final bpmAc = 60 * sampleHz / lag;
    // 2. peaks (minimum distance 0.6 x the autocorrelation period)
    final minDist = (lag * 0.6).floor();
    final peaks = <int>[];
    for (var i = 1; i < y.length - 1; i++) {
      if (y[i] > 0 && y[i] > y[i - 1] && y[i] >= y[i + 1]) {
        if (peaks.isNotEmpty && i - peaks.last < minDist) {
          if (y[i] > y[peaks.last]) peaks[peaks.length - 1] = i;
        } else {
          peaks.add(i);
        }
      }
    }
    final ibi = [for (var i = 1; i < peaks.length; i++) (peaks[i] - peaks[i - 1]) / sampleHz];
    final details = {...base, 'quality': double.parse(best.toStringAsFixed(3)),
        'bpm_autocorr': double.parse(bpmAc.toStringAsFixed(1)), 'beats': peaks.length};
    if (ibi.length < 5) {
      return PpgResult(PpgVerdict.inconclusive, quality: best, reason: 'too few heartbeats detected', details: details);
    }
    final sorted = [...ibi]..sort();
    final median = sorted[sorted.length ~/ 2];
    final bpmPk = 60 / median;
    final mean = ibi.reduce((p, q) => p + q) / ibi.length;
    final cv = _std(ibi) / mean;
    details['bpm_peaks'] = double.parse(bpmPk.toStringAsFixed(1));
    details['ibi_cv'] = double.parse(cv.toStringAsFixed(3));
    if (best < minQuality) {
      return PpgResult(PpgVerdict.inconclusive, quality: best,
          reason: 'signal too noisy (quality ${(best * 100).round()}%) - keep the finger still', details: details);
    }
    if ((bpmAc - bpmPk).abs() / bpmAc > maxDisagreement) {
      return PpgResult(PpgVerdict.inconclusive, quality: best,
          reason: 'the two pulse estimates disagree (${bpmAc.round()} vs ${bpmPk.round()} bpm)', details: details);
    }
    final bpm = ((bpmAc + bpmPk) / 2).round();
    if (bpm < minBpm || bpm > maxBpm) {
      return PpgResult(PpgVerdict.inconclusive, quality: best, reason: 'rate outside $minBpm-$maxBpm bpm', details: details);
    }
    return PpgResult(PpgVerdict.valid, bpm: bpm, quality: best, details: details);
  }

  /// Linear interpolation onto a uniform 30 Hz grid (camera frame times jitter).
  List<double> _resample() {
    final out = <double>[];
    const dt = 1 / sampleHz;
    var j = 0;
    for (var s = _t.first; s <= _t.last; s += dt) {
      while (j < _t.length - 2 && _t[j + 1] < s) {
        j++;
      }
      final t0 = _t[j], t1 = _t[j + 1];
      final w = t1 > t0 ? ((s - t0) / (t1 - t0)).clamp(0.0, 1.0) : 0.0;
      out.add(_v[j] + (_v[j + 1] - _v[j]) * w);
    }
    return out;
  }

  /// Removes the slow baseline (1.5 s moving average) and smooths (5-sample moving average). The camera signal is
  /// inverted: more blood in the fingertip = darker image, so a heartbeat is a dip; flipping makes it a peak.
  static List<double> _detrend(List<double> x) {
    final base = _movingAverage(x, (sampleHz * 1.5).round());
    final hp = [for (var i = 0; i < x.length; i++) base[i] - x[i]];
    return _movingAverage(hp, 5);
  }

  static List<double> _movingAverage(List<double> x, int w) {
    final out = List<double>.filled(x.length, 0);
    final half = w ~/ 2;
    final prefix = List<double>.filled(x.length + 1, 0);
    for (var i = 0; i < x.length; i++) {
      prefix[i + 1] = prefix[i] + x[i];
    }
    for (var i = 0; i < x.length; i++) {
      final lo = math.max(0, i - half), hi = math.min(x.length, i + half + 1);
      out[i] = (prefix[hi] - prefix[lo]) / (hi - lo);
    }
    return out;
  }

  static double _std(List<double> x) {
    final m = x.reduce((a, b) => a + b) / x.length;
    return math.sqrt(x.fold<double>(0, (a, b) => a + (b - m) * (b - m)) / x.length);
  }
}
