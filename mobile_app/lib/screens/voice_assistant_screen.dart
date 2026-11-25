import 'dart:async';

import 'package:flutter/material.dart';
import 'package:geolocator/geolocator.dart';

import '../models/vital_reading.dart';
import '../services/api_service.dart';
import '../services/voice_service.dart';
import '../theme.dart';
import '../widgets/common.dart';
import 'tracking_screen.dart';

/// Hands-free assistant: press the big microphone (or open it by a help phrase in hands-free mode) and speak.
/// * Emergency words are acted on at once - even before the sentence ends - with a 5 second cancel countdown.
/// * "I need a medical test", "check my heart rate", "explain my results", "talk to a doctor" are commands.
/// * Anything else goes to the medical knowledge assistant; the answer is spoken.
class VoiceAssistantScreen extends StatefulWidget {
  final bool startListening;
  const VoiceAssistantScreen({super.key, this.startListening = true});
  @override
  State<VoiceAssistantScreen> createState() => _VoiceAssistantScreenState();
}

class _Line {
  final String text;
  final bool mine;
  _Line(this.text, this.mine);
}

class _VoiceAssistantScreenState extends State<VoiceAssistantScreen> {
  final _voice = VoiceService.instance;
  final _lines = <_Line>[];
  String _partial = '';
  bool _listening = false, _busy = false;
  int? _countdown;
  Timer? _timer;
  List<String> _emSymptoms = [];

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) async {
      if (widget.startListening) {
        await _voice.say('I am listening. Tell me how I can help.', force: true);
        _listen();
      }
    });
  }

  @override
  void dispose() {
    _timer?.cancel();
    _voice.stop();
    _voice.stopSpeaking();
    super.dispose();
  }

  void _add(String t, bool mine) => setState(() => _lines.add(_Line(t, mine)));

  Future<void> _reply(String t) async {
    _add(t, false);
    await _voice.say(t);
  }

  Future<void> _listen() async {
    if (_listening || _countdown != null) return;
    if (!await _voice.ensureStt()) {
      return _reply('Speech recognition is not available on this phone. You can use the large buttons instead.');
    }
    setState(() {
      _listening = true;
      _partial = '';
    });
    var urgent = false;
    final text = await _voice.listenOnce(onPartial: (t) {
      if (mounted) setState(() => _partial = t);
      if (!urgent && VoiceIntents.isEmergency(t)) {
        urgent = true;
        _voice.stop(); // clearly an emergency: act now, do not wait for the full sentence
      }
    });
    if (!mounted) return;
    final said = text.isEmpty ? _partial : text;
    setState(() {
      _listening = false;
      _partial = '';
    });
    if (said.trim().isEmpty) return _reply('I did not hear anything. Press the microphone and speak again.');
    _add(said, true);
    await _handle(VoiceIntents.parse(said), said);
  }

  Future<void> _handle(VoiceIntent i, String said) async {
    switch (i.kind) {
      case 'emergency':
        _emSymptoms = i.symptoms.isEmpty ? ['emergency_help'] : i.symptoms;
        return _startCountdown();
      case 'test':
        final codes = i.testCode == null || i.testCode == 'all'
            ? ['temperature', 'heart_rate', 'spo2', 'blood_pressure', 'respiratory']
            : [i.testCode!];
        try {
          final r = await ApiService.instance.requestTests(codes);
          return _reply('I sent your request for ${r.testNames}. Reference ${r.reference}. '
              'Medical staff will accept it and start the test.');
        } catch (e) {
          return _reply('I could not request the test. $e');
        }
      case 'results':
        return _readResults();
      case 'contact':
        try {
          await ApiService.instance.post('/monitoring/events/', {'kind': 'contact_request', 'text': said});
          return _reply('I have asked a health professional to contact you. If it is an emergency, say "emergency".');
        } catch (e) {
          return _reply('I could not send the request. $e');
        }
      case 'repeat':
        final last = _lines.lastWhere((l) => !l.mine, orElse: () => _Line('Tell me how I can help.', false));
        return _voice.say(last.text, force: true);
      default:
        setState(() => _busy = true);
        try {
          final a = await ApiService.instance.ask(said);
          await _reply((a['ok'] == true ? a['answer'] : (a['error'] ?? 'I could not find an answer.')).toString());
          if (a['emergency'] == true) await _reply('This may be an emergency. Say "emergency" or press the red button to get help.');
        } catch (e) {
          await _reply('I am offline right now. In an emergency, call 912.');
        } finally {
          if (mounted) setState(() => _busy = false);
        }
    }
  }

  Future<void> _readResults() async {
    try {
      final rs = await ApiService.instance.latestVitals(limit: 10);
      if (rs.isEmpty) return _reply('You have no recorded measurements yet.');
      final l = latestByVital(rs);
      final names = {'temperature': 'temperature', 'heart_rate': 'heart rate', 'spo2': 'oxygen level',
        'blood_pressure': 'blood pressure', 'respiratory': 'breathing rate'};
      final parts = [
        for (final e in l.entries)
          '${names[e.key]} ${e.value.value}${e.value.simulated ? ', which is a simulated test value' : ''}'
      ];
      return _reply('Your latest readings: ${parts.join('; ')}. A health professional will explain what they mean for you.');
    } catch (e) {
      return _reply('I could not load your results. $e');
    }
  }

  void _startCountdown() {
    setState(() => _countdown = 5);
    _voice.say('I am getting emergency help for you in 5 seconds. Say stop or press cancel to stop.', force: true).then((_) {
      if (_countdown == null || !mounted) return;
      _voice.listenOnce(max: const Duration(seconds: 4), onPartial: (t) {
        if (_countdown != null && RegExp(r'\b(stop|cancel|no|annuler|arrête)\b', caseSensitive: false).hasMatch(t)) {
          _voice.stop();
          _cancel();
        }
      });
    });
    _timer = Timer.periodic(const Duration(seconds: 1), (t) {
      if (!mounted) return t.cancel();
      if (_countdown! <= 1) {
        t.cancel();
        _sendEmergency();
      } else {
        setState(() => _countdown = _countdown! - 1);
      }
    });
  }

  void _cancel() {
    _timer?.cancel();
    setState(() => _countdown = null);
    _reply('Cancelled. I did not call for help.');
  }

  Future<void> _sendEmergency() async {
    setState(() {
      _countdown = null;
      _busy = true;
    });
    try {
      Position? pos;
      final perm = await Geolocator.checkPermission();
      if (perm == LocationPermission.always || perm == LocationPermission.whileInUse) {
        pos = await Geolocator.getCurrentPosition().timeout(const Duration(seconds: 8));
      }
      if (pos != null) {
        final em = await ApiService.instance.createEmergency({
          'emergency_type': _emSymptoms.contains('cannot_breathe') ? 'respiratory'
              : _emSymptoms.contains('chest_pain') ? 'cardiac' : 'other',
          'description': 'Requested by voice: ${_lines.where((l) => l.mine).map((l) => l.text).join(' / ')}',
          'latitude': pos.latitude, 'longitude': pos.longitude, 'location_accuracy_m': pos.accuracy,
          'location_source': 'gps', 'location_consent': true,
          'breathing_difficulty': _emSymptoms.contains('cannot_breathe'), 'chest_pain': _emSymptoms.contains('chest_pain'),
        });
        await _reply('Help has been requested. Your location was shared with the emergency team. Reference ${em.reference}.');
        if (mounted) Navigator.push(context, MaterialPageRoute(builder: (_) => TrackingScreen(emergencyId: em.id)));
      } else {
        // no location permission: the backend escalates at once and the operator calls to confirm where you are
        await ApiService.instance.post('/monitoring/events/', {'kind': 'patient_report', 'symptoms': _emSymptoms});
        await _reply('Help has been requested. Your location was not shared, so the emergency team will call you to '
            'confirm where you are.');
      }
    } catch (e) {
      await _voice.say('I could not reach the emergency team. Call 912 now.', force: true);
      if (mounted) toast(context, e, error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Voice assistant')),
      body: Column(children: [
        Expanded(
          child: ListView(padding: const EdgeInsets.all(16), children: [
            if (_lines.isEmpty)
              const Text('Say for example: "I need emergency assistance", "Check my heart rate", '
                  '"Explain my results", "What is malaria?"', style: TextStyle(fontSize: 16, color: Colors.black54)),
            for (final l in _lines)
              Align(
                alignment: l.mine ? Alignment.centerRight : Alignment.centerLeft,
                child: Container(
                  margin: const EdgeInsets.only(bottom: 10),
                  padding: const EdgeInsets.all(14),
                  constraints: BoxConstraints(maxWidth: MediaQuery.of(context).size.width * .85),
                  decoration: BoxDecoration(
                      color: l.mine ? AppColors.primary : Colors.white,
                      borderRadius: BorderRadius.circular(18),
                      border: l.mine ? null : Border.all(color: const Color(0xFFE4E7EC))),
                  child: Text(l.text, style: TextStyle(fontSize: 17, height: 1.4, color: l.mine ? Colors.white : null)),
                ),
              ),
            if (_busy) const LinearProgressIndicator(),
          ]),
        ),
        if (_countdown != null)
          Semantics(
            liveRegion: true,
            label: 'Calling for help in $_countdown seconds',
            child: Container(
              color: AppColors.emergency,
              padding: const EdgeInsets.all(20),
              child: Column(children: [
                Text('Getting help in $_countdown', style: const TextStyle(color: Colors.white, fontSize: 28, fontWeight: FontWeight.w900)),
                const SizedBox(height: 12),
                FilledButton(
                  style: FilledButton.styleFrom(backgroundColor: Colors.white, foregroundColor: AppColors.emergency,
                      minimumSize: const Size.fromHeight(64)),
                  onPressed: _cancel,
                  child: const Text('CANCEL', style: TextStyle(fontSize: 22, fontWeight: FontWeight.w900)),
                ),
              ]),
            ),
          )
        else
          SafeArea(
            top: false,
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(children: [
                if (_listening)
                  Semantics(
                    liveRegion: true,
                    child: Row(mainAxisAlignment: MainAxisAlignment.center, children: [
                      const Icon(Icons.fiber_manual_record, color: AppColors.emergency, size: 14),
                      const SizedBox(width: 6),
                      Flexible(child: Text(_partial.isEmpty ? 'Microphone on - listening' : _partial,
                          style: const TextStyle(fontSize: 16))),
                    ]),
                  ),
                const SizedBox(height: 10),
                Semantics(
                  button: true,
                  label: _listening ? 'Stop listening' : 'Press and speak',
                  child: GestureDetector(
                    onTap: _listening ? () => _voice.stop() : _listen,
                    child: Container(
                      width: 140,
                      height: 140,
                      decoration: BoxDecoration(
                        shape: BoxShape.circle,
                        color: _listening ? AppColors.emergency : AppColors.primary,
                        boxShadow: [BoxShadow(color: (_listening ? AppColors.emergency : AppColors.primary).withValues(alpha: .35), blurRadius: 24)],
                      ),
                      child: Icon(_listening ? Icons.stop : Icons.mic, color: Colors.white, size: 72),
                    ),
                  ),
                ),
                const SizedBox(height: 8),
                const Text('Automatic assistant - not a doctor', style: TextStyle(fontSize: 12, color: Colors.black54)),
              ]),
            ),
          ),
      ]),
    );
  }
}
