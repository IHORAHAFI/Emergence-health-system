import 'dart:async';
import 'dart:io' show Platform;

import 'package:camera/camera.dart';
import 'package:flutter/material.dart';

import '../services/measurement_outbox.dart';
import '../services/ppg.dart';
import '../services/voice_service.dart';
import '../theme.dart';
import '../widgets/measure_widgets.dart';

/// Phone-camera heart-rate ESTIMATE (PPG), completed within 60 seconds and sent to the operator automatically.
///
///   screen opens      -> camera permission + initialisation (before the countdown)
///   Start             -> 60 s countdown, flashlight on, frames analysed live
///   >= 25 s of clean, stable signal  -> result (usually 30-45 s); collection stops at 52 s at the latest
///   result / failure  -> shown and sent at once (no Submit button); a poor signal is sent as INCONCLUSIVE with no
///                        value - a number is never invented to meet the deadline.
class HeartRateScreen extends StatefulWidget {
  const HeartRateScreen({super.key});
  @override
  State<HeartRateScreen> createState() => _HeartRateScreenState();
}

enum _Phase { preparing, ready, unavailable, measuring, done }

class _HeartRateScreenState extends State<HeartRateScreen> {
  static const _collectUntil = Duration(seconds: 52); // leaves time to analyse + send inside 60 s
  static const _minAccept = Duration(seconds: 25);

  CameraController? _cam;
  _Phase _phase = _Phase.preparing;
  String? _problem;
  bool _torch = false;
  final _clock = Stopwatch();
  Timer? _tick;
  PpgAnalyzer? _ppg;
  int? _lastValidBpm;
  String _hint = '';
  bool _busyFrame = false;
  MeasureResult? _result;
  ValueNotifier<SendState>? _send;

  @override
  void initState() {
    super.initState();
    _prepare();
  }

  Future<void> _prepare() async {
    setState(() {
      _phase = _Phase.preparing;
      _problem = null;
    });
    try {
      final cams = await availableCameras();
      final back = cams.where((c) => c.lensDirection == CameraLensDirection.back).firstOrNull;
      if (back == null) throw CameraException('NoBackCamera', 'This phone has no rear camera.');
      final c = CameraController(back, ResolutionPreset.low, enableAudio: false,
          imageFormatGroup: Platform.isIOS ? ImageFormatGroup.bgra8888 : ImageFormatGroup.yuv420);
      await c.initialize(); // asks for camera permission
      _cam = c;
      if (mounted) setState(() => _phase = _Phase.ready);
    } on CameraException catch (e) {
      final denied = e.code.contains('AccessDenied') || e.code.contains('Restricted');
      if (mounted) {
        setState(() {
          _phase = _Phase.unavailable;
          _problem = denied
              ? 'Camera permission was denied. Allow the camera in the phone settings to measure your heart rate.'
              : 'The camera cannot be used: ${e.description ?? e.code}';
        });
      }
    }
  }

  Future<void> _start() async {
    final c = _cam;
    if (c == null || !c.value.isInitialized) return;
    _ppg = PpgAnalyzer();
    _lastValidBpm = null;
    _result = null;
    _send = null;
    _clock
      ..reset()
      ..start();
    setState(() {
      _phase = _Phase.measuring;
      _hint = 'Cover the rear camera and the flashlight completely with your fingertip.';
    });
    VoiceService.instance.say('Cover the camera and the light with your fingertip, and keep still.');
    try {
      await c.setFlashMode(FlashMode.torch);
      _torch = true;
    } catch (_) {
      _torch = false; // no flashlight: still possible in bright light, quality check decides
    }
    try {
      await c.startImageStream(_onFrame);
    } catch (e) {
      return _finish(MeasureResult(MeasureOutcome.failed, unit: 'bpm', reason: 'camera stream could not start: $e'));
    }
    _tick = Timer.periodic(const Duration(milliseconds: 500), (_) => _evaluate());
  }

  void _onFrame(CameraImage img) {
    if (_phase != _Phase.measuring || _busyFrame) return;
    _busyFrame = true;
    try {
      final f = ppgFrameFrom(img, _clock.elapsed);
      if (f != null) _ppg?.add(f);
    } finally {
      _busyFrame = false;
    }
  }

  Future<void> _evaluate() async {
    final p = _ppg;
    if (p == null || _phase != _Phase.measuring) return;
    final t = _clock.elapsed;
    String hint;
    if (!p.fingerPresent) {
      hint = p.framesWithFinger == 0
          ? 'No fingertip detected. Cover the rear camera${_torch ? ' and the flashlight' : ''} completely.'
          : 'Finger moved - cover the camera again and keep still.';
    } else if (p.signalSeconds < p.minSignal.inSeconds) {
      hint = 'Measuring... keep your finger still (${p.signalSeconds.toStringAsFixed(0)} s of signal)';
    } else {
      final r = p.analyze();
      if (r.isValid) {
        final stable = _lastValidBpm != null && (r.bpm! - _lastValidBpm!).abs() <= 4;
        _lastValidBpm = r.bpm;
        if (stable && p.signalSeconds >= _minAccept.inSeconds) {
          return _finish(MeasureResult(MeasureOutcome.estimated, value: r.bpm, unit: 'bpm'), r);
        }
        hint = 'Signal found - confirming...';
      } else {
        _lastValidBpm = null;
        hint = 'Keep still: ${r.reason}';
      }
    }
    if (t >= _collectUntil) return _deadline();
    if (mounted) setState(() => _hint = hint);
  }

  Future<void> _deadline() async {
    final p = _ppg!;
    final r = p.analyze();
    if (r.isValid) {
      return _finish(MeasureResult(MeasureOutcome.estimated, value: r.bpm, unit: 'bpm'), r);
    }
    if (p.framesWithFinger == 0) {
      return _finish(const MeasureResult(MeasureOutcome.failed, unit: 'bpm',
          reason: 'Timeout: no fingertip was detected on the camera within 60 seconds.'), r);
    }
    _finish(MeasureResult(MeasureOutcome.inconclusive, unit: 'bpm',
        reason: 'Timeout: the signal could not be validated (${r.reason}). No value was recorded.'), r);
  }

  Future<void> _finish(MeasureResult result, [PpgResult? ppg]) async {
    if (_phase == _Phase.done) return;
    _tick?.cancel();
    final duration = _clock.elapsed;
    _clock.stop();
    setState(() {
      _phase = _Phase.done;
      _result = result;
    });
    await _stopCamera();
    VoiceService.instance.say(result.valid
        ? 'Estimated heart rate ${result.value} beats per minute. Sending to the operator.'
        : 'The measurement did not succeed. You can try again.');
    // Automatic submission: no Submit button.
    final s = await MeasurementOutbox.instance.submit({
      'client_id': MeasurementOutbox.newClientId(),
      'kind': 'heart_rate',
      'status': result.outcome.name,
      'value': result.valid ? result.value : null,
      'source': 'phone_camera',
      'method': 'ppg',
      'measured_at': DateTime.now().toUtc().toIso8601String(),
      'duration_s': double.parse((duration.inMilliseconds / 1000).toStringAsFixed(1)),
      if (!result.valid) 'failure_reason': result.reason,
      'device': {'name': 'Phone rear camera', 'platform': Platform.operatingSystem,
          'camera': _cam?.description.name ?? '', 'flashlight': _torch},
      'quality': ppg?.details ?? {},
    });
    if (mounted) setState(() => _send = s);
  }

  Future<void> _stopCamera() async {
    final c = _cam;
    if (c == null) return;
    try {
      if (c.value.isStreamingImages) await c.stopImageStream();
    } catch (_) {}
    try {
      if (_torch) await c.setFlashMode(FlashMode.off);
    } catch (_) {}
  }

  @override
  void dispose() {
    _tick?.cancel();
    _stopCamera().whenComplete(() => _cam?.dispose());
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final remaining = measurementDeadline - _clock.elapsed;
    return Scaffold(
      appBar: AppBar(title: const Text('Heart rate (phone camera)')),
      body: ListView(padding: const EdgeInsets.all(16), children: [
        Card(
          child: Padding(
            padding: const EdgeInsets.all(14),
            child: Text(
                'Your heart rate is ESTIMATED from the colour of your fingertip (not a medical device). The result '
                'is sent to the emergency operator automatically. Takes at most 60 seconds.',
                style: TextStyle(color: Colors.grey.shade800)),
          ),
        ),
        const SizedBox(height: 16),
        switch (_phase) {
          _Phase.preparing => const Center(
              child: Padding(padding: EdgeInsets.all(24), child: Text('Preparing the camera...'))),
          _Phase.unavailable => Column(children: [
              Text(_problem ?? '', style: const TextStyle(color: AppColors.emergency, fontSize: 16)),
              const SizedBox(height: 12),
              OutlinedButton.icon(onPressed: _prepare, icon: const Icon(Icons.refresh), label: const Text('Try again')),
            ]),
          _Phase.ready => Column(children: [
              const StepLine('Camera ready', done: true),
              const StepLine('Press Start, then cover the rear camera and flashlight with a fingertip'),
              const StepLine('Keep still for about 30-45 seconds'),
              const SizedBox(height: 16),
              FilledButton.icon(
                  style: FilledButton.styleFrom(minimumSize: const Size.fromHeight(56)),
                  onPressed: _start,
                  icon: const Icon(Icons.favorite),
                  label: const Text('Start measurement', style: TextStyle(fontSize: 18))),
            ]),
          _Phase.measuring => Column(children: [
              CountdownRing(remaining: remaining, caption: _hint),
              const SizedBox(height: 12),
              if (_ppg != null)
                LinearProgressIndicator(value: (_ppg!.signalSeconds / _minAccept.inSeconds).clamp(0.0, 1.0)),
            ]),
          _Phase.done => Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
              const StepLine('Measurement completed', done: true),
              ResultCard(
                  result: _result!,
                  label: 'Heart rate',
                  note: _result!.valid ? 'Estimated with the phone camera (PPG). Not a medical-grade measurement.' : null),
              if (_send != null) SendStatusCard(state: _send!),
              const SizedBox(height: 8),
              OutlinedButton.icon(
                  onPressed: () => _cam == null ? _prepare() : setState(() => _phase = _Phase.ready),
                  icon: const Icon(Icons.replay),
                  label: Text(_result!.valid ? 'Measure again' : 'Try again')),
            ]),
        },
      ]),
    );
  }
}

/// One PPG sample from a camera frame: mean brightness of the centre of the image, and whether a lit fingertip
/// covers the lens (strongly red, evenly lit). Android YUV420: luma + Cr (red chroma). iOS BGRA8888: R/G/B.
PpgFrame? ppgFrameFrom(CameraImage img, Duration t) {
  final w = img.width, h = img.height;
  if (w == 0 || h == 0 || img.planes.isEmpty) return null;
  final x0 = w ~/ 4, x1 = w * 3 ~/ 4, y0 = h ~/ 4, y1 = h * 3 ~/ 4;
  const step = 6;
  var n = 0;
  double sumY = 0, sumRed = 0;
  if (img.format.group == ImageFormatGroup.bgra8888) {
    final p = img.planes[0];
    for (var y = y0; y < y1; y += step) {
      for (var x = x0; x < x1; x += step) {
        final i = y * p.bytesPerRow + x * 4;
        if (i + 2 >= p.bytes.length) continue;
        final b = p.bytes[i], g = p.bytes[i + 1], r = p.bytes[i + 2];
        sumY += 0.299 * r + 0.587 * g + 0.114 * b;
        // red dominance, on the same 0..255 scale as Cr: 128 + 0.5R - 0.419G - 0.081B
        sumRed += 128 + 0.5 * r - 0.419 * g - 0.081 * b;
        n++;
      }
    }
  } else {
    if (img.planes.length < 3) return null;
    final yp = img.planes[0], vp = img.planes[2];
    final vStride = vp.bytesPerPixel ?? 1;
    for (var y = y0; y < y1; y += step) {
      for (var x = x0; x < x1; x += step) {
        final yi = y * yp.bytesPerRow + x;
        final vi = (y ~/ 2) * vp.bytesPerRow + (x ~/ 2) * vStride;
        if (yi >= yp.bytes.length || vi >= vp.bytes.length) continue;
        sumY += yp.bytes[yi];
        sumRed += vp.bytes[vi];
        n++;
      }
    }
  }
  if (n == 0) return null;
  final luma = sumY / n, red = sumRed / n;
  return PpgFrame(luma, fingerCovers(luma, red), t);
}

/// A fingertip lit from behind fills the image with red: high red chroma and not dark.
bool fingerCovers(double luma, double redChroma) => redChroma > 160 && luma > 25;
