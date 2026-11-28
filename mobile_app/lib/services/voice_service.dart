import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter_tts/flutter_tts.dart';
import 'package:speech_to_text/speech_recognition_result.dart';
import 'package:speech_to_text/speech_to_text.dart';

import 'prefs_service.dart';

/// Text-to-Speech + Speech-to-Text with the patient's language and speed.
///
/// Privacy: audio is never recorded or uploaded by this app. Recognition uses the phone's speech service;
/// on-device recognition is tried first and the platform service (which may use the network) is the fallback.
/// Only the recognised TEXT is sent to the backend, and only when the patient asks a question or answers.
class VoiceService {
  VoiceService._();
  static final instance = VoiceService._();

  final _tts = FlutterTts();
  final _stt = SpeechToText();
  bool _sttReady = false, _onDeviceFailed = false;
  final listening = ValueNotifier<bool>(false);
  String? lastError;

  static const _locales = {'en': 'en-US', 'fr': 'fr-FR', 'rw': 'rw-RW'};

  AppPrefs get _p => PrefsService.instance.p;

  Future<void> _configureTts() async {
    var lang = _locales[_p.language] ?? 'en-US';
    try {
      if (await _tts.isLanguageAvailable(lang) != true) lang = 'en-US'; // e.g. no Kinyarwanda voice installed
      await _tts.setLanguage(lang);
      await _tts.setSpeechRate(_p.voiceRate);
      await _tts.awaitSpeakCompletion(true);
    } catch (_) {}
  }

  /// Speaks [text] slowly and clearly (waits until finished). Does nothing if spoken responses are off,
  /// unless [force] (e.g. an emergency confirmation, which must always be heard).
  Future<void> say(String text, {bool force = false}) async {
    if (text.trim().isEmpty || (!_p.spokenResponses && !force)) return;
    await _configureTts();
    try {
      await _tts.stop();
      await _tts.speak(text.replaceAll(RegExp(r'\[\d+\]'), ''));
    } catch (_) {}
  }

  Future<void> stopSpeaking() async {
    try {
      await _tts.stop();
    } catch (_) {}
  }

  /// Pause speech where the platform supports it (Android 8+ / iOS); otherwise stops (replay starts again).
  Future<void> pauseSpeaking() async {
    try {
      final r = await _tts.pause();
      if (r != 1) await _tts.stop();
    } catch (_) {
      await stopSpeaking();
    }
  }

  /// Confidence of the last final recognition (0..1), or null when the speech service does not report one.
  double? lastConfidence;
  static const minConfidence = 0.5;

  Future<bool> ensureStt() async {
    if (_sttReady) return true;
    try {
      _sttReady = await _stt.initialize(onError: (e) {
        lastError = e.errorMsg;
        if (e.errorMsg.contains('language') || e.errorMsg.contains('network') || e.errorMsg.contains('11')) {
          _onDeviceFailed = true;
        }
        listening.value = false;
      }, onStatus: (s) => listening.value = s == 'listening');
    } catch (e) {
      lastError = '$e';
      _sttReady = false;
    }
    return _sttReady;
  }

  String? _sttLocale() => _locales[_p.language];

  /// Listens once. [onPartial] gets words as they are recognised, so callers can react to an emergency phrase
  /// before the sentence is finished. Returns the final text ('' if nothing / not available).
  Future<String> listenOnce({void Function(String partial)? onPartial, Duration max = const Duration(seconds: 12),
      bool preferOnDevice = true}) async {
    if (!await ensureStt()) return '';
    await stopSpeaking();
    final done = Completer<String>();
    var latest = '';
    lastConfidence = null;
    Future<void> start(bool onDevice) => _stt.listen(
          onResult: (SpeechRecognitionResult r) {
            latest = r.recognizedWords;
            onPartial?.call(latest);
            if (r.finalResult) lastConfidence = r.hasConfidenceRating ? r.confidence : null;
            if (r.finalResult && !done.isCompleted) done.complete(latest);
          },
          listenOptions: SpeechListenOptions(
            partialResults: true,
            onDevice: onDevice,
            listenFor: max,
            pauseFor: const Duration(seconds: 3),
            localeId: _sttLocale(),
            cancelOnError: true,
          ),
        );
    try {
      await start(preferOnDevice && !_onDeviceFailed);
    } catch (_) {
      _onDeviceFailed = true;
      try {
        await start(false);
      } catch (e) {
        lastError = '$e';
        return '';
      }
    }
    listening.value = true;
    final result = await done.future.timeout(max + const Duration(seconds: 2), onTimeout: () => latest);
    await stop();
    return result;
  }

  Future<void> stop() async {
    try {
      await _stt.stop();
    } catch (_) {}
    listening.value = false;
  }
}

/// What the patient meant. Simple keyword rules (English + French), deliberately conservative:
/// emergency words always win, and an unclear answer is treated as "not understood", never as "I'm fine".
class VoiceIntent {
  final String kind; // emergency | yes | no | help | repeat | test | results | contact | question | unknown
  final String? testCode;
  final List<String> symptoms;
  const VoiceIntent(this.kind, {this.testCode, this.symptoms = const []});
}

class VoiceIntents {
  static const _emergency = [
    'emergency', 'help me', 'call an ambulance', 'ambulance', "can't breathe", 'cannot breathe', 'not breathing',
    'chest pain', 'heart attack', 'stroke', 'bleeding', 'unconscious', 'i fell', 'fallen', 'seizure',
    'call emergency', 'medical assistance', 'urgence', 'au secours', 'aidez-moi', 'je ne peux pas respirer',
    'douleur thoracique',
    // Kinyarwanda recognition words (input only, never used to write replies) - to be checked by a native speaker
    'mfasha', 'ubutabazi',
  ];
  static const _symptomWords = {
    'chest pain': 'chest_pain', 'douleur thoracique': 'chest_pain', "can't breathe": 'cannot_breathe',
    'cannot breathe': 'cannot_breathe', 'hard to breathe': 'cannot_breathe', 'short of breath': 'cannot_breathe',
    'difficulty breathing': 'cannot_breathe', 'trouble breathing': 'cannot_breathe', 'du mal à respirer': 'cannot_breathe',
    'dizzy': 'dizzy', 'vertige': 'dizzy', 'weak': 'weak', 'faible': 'weak', 'fever': 'fever', 'fièvre': 'fever',
    'pain': 'pain', 'hurt': 'pain', 'douleur': 'pain', 'bleeding': 'severe_bleeding', 'fell': 'cannot_move',
    "can't move": 'cannot_move', 'vomit': 'nausea',
  };
  static const _yes = ['yes', 'yeah', 'yep', 'i am okay', "i'm okay", 'i am ok', "i'm ok", 'fine', 'i am fine',
    "i'm fine", 'good', 'oui', 'ça va', 'ca va', 'yego', 'ndi muzima'];
  static const _no = ['no', 'not okay', 'not ok', 'not good', 'not fine', 'bad', 'non', 'pas bien', 'oya'];
  static const _tests = {
    'heart rate': 'heart_rate', 'pulse': 'heart_rate', 'temperature': 'temperature', 'oxygen': 'spo2',
    'blood pressure': 'blood_pressure', 'breathing rate': 'respiratory', 'all vital': 'all', 'vital signs': 'all',
  };

  static List<String> symptoms(String t) {
    final q = t.toLowerCase();
    return {for (final e in _symptomWords.entries) if (q.contains(e.key)) e.value}.toList();
  }

  static bool isEmergency(String t) {
    final q = t.toLowerCase();
    return _emergency.any(q.contains);
  }

  static VoiceIntent parse(String text) {
    final q = ' ${text.toLowerCase().trim()} ';
    if (q.trim().isEmpty) return const VoiceIntent('unknown');
    if (isEmergency(q)) return VoiceIntent('emergency', symptoms: symptoms(q));
    if (RegExp(r'\b(repeat|again|say that again|what did you say|répète|repete)\b').hasMatch(q)) {
      return const VoiceIntent('repeat');
    }
    if (RegExp(r'\b(doctor|nurse|talk to (a |someone)|médecin|docteur)\b').hasMatch(q)) return const VoiceIntent('contact');
    if (RegExp(r'\b(test|check|measure|mesurer)\b').hasMatch(q)) {
      for (final e in _tests.entries) {
        if (q.contains(e.key)) return VoiceIntent('test', testCode: e.value);
      }
      return const VoiceIntent('test');
    }
    if (RegExp(r'\b(result|results|résultat|readings)\b').hasMatch(q)) return const VoiceIntent('results');
    final no = _no.any((w) => RegExp('\\b${RegExp.escape(w)}\\b').hasMatch(q));
    final yes = _yes.any((w) => RegExp('\\b${RegExp.escape(w)}\\b').hasMatch(q));
    if (no) return VoiceIntent('no', symptoms: symptoms(q)); // "no, not okay" beats an accidental "okay"
    if (yes) return const VoiceIntent('yes');
    if (symptoms(q).isNotEmpty) return VoiceIntent('no', symptoms: symptoms(q));
    return const VoiceIntent('question');
  }
}

@visibleForTesting
VoiceIntent parseForTest(String t) => VoiceIntents.parse(t);
