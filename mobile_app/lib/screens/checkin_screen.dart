import 'package:flutter/material.dart';

import '../services/api_service.dart';
import '../services/prefs_service.dart';
import '../services/voice_service.dart';
import '../theme.dart';
import '../widgets/common.dart';
import '../widgets/communication_board.dart';

/// AI-initiated conversation. Opened automatically when the app is on screen (or from the notification).
/// The assistant speaks one question at a time and accepts a spoken answer OR the YES / NO / symptom board.
/// If nobody answers, the SERVER repeats the question a few times and then alerts humans - nothing here
/// has to keep running for that to happen.
class CheckInScreen extends StatefulWidget {
  final Map<String, dynamic> event;
  const CheckInScreen({super.key, required this.event});
  @override
  State<CheckInScreen> createState() => _CheckInScreenState();
}

class _CheckInScreenState extends State<CheckInScreen> {
  late String _question = widget.event['prompt'] ?? 'Are you feeling okay?';
  String _heard = '';
  String? _reply;
  bool _sending = false, _done = false, _listening = false;
  final _voice = VoiceService.instance;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _askAloud());
  }

  @override
  void dispose() {
    _voice.stop();
    _voice.stopSpeaking();
    super.dispose();
  }

  Future<void> _askAloud() async {
    await _voice.say(_question, force: true);
    if (mounted && !_done && PrefsService.instance.p.voiceAssistant) _listen();
  }

  Future<void> _listen() async {
    setState(() {
      _listening = true;
      _heard = '';
    });
    var urgent = false;
    final text = await _voice.listenOnce(onPartial: (t) {
      if (mounted) setState(() => _heard = t);
      if (!urgent && VoiceIntents.isEmergency(t)) {
        urgent = true; // do not wait for the whole sentence
        _voice.stop();
      }
    });
    if (!mounted) return;
    setState(() => _listening = false);
    final said = text.isEmpty ? _heard : text;
    final intent = VoiceIntents.parse(said);
    switch (intent.kind) {
      case 'emergency':
        return _send('help', symptoms: intent.symptoms, text: said, channel: 'voice');
      case 'yes':
        return _send('ok', text: said, channel: 'voice');
      case 'no':
        return _send('not_ok', symptoms: intent.symptoms, text: said, channel: 'voice');
      case 'repeat':
        return _askAloud();
      default:
        if (said.isNotEmpty) {
          await _voice.say("Sorry, I did not understand. Please say yes or no, or use the big buttons.", force: true);
        }
    }
  }

  Future<void> _send(String answer, {List<String> symptoms = const [], int? pain, String text = '', String channel = 'buttons'}) async {
    if (_sending) return;
    setState(() => _sending = true);
    try {
      final r = Map<String, dynamic>.from(await ApiService.instance.post('/monitoring/events/${widget.event['id']}/respond/', {
        'answer': answer, 'symptoms': symptoms, if (pain != null) 'pain': pain, 'text': text, 'channel': channel,
      }));
      final next = (r['next_prompt'] ?? '').toString();
      final guidance = (r['guidance'] ?? '').toString();
      final ev = Map<String, dynamic>.from(r['event'] ?? {});
      setState(() {
        _reply = [next, guidance].where((x) => x.isNotEmpty).join('\n\n');
        if (ev['status'] == 'awaiting_response') {
          _question = ev['prompt'] ?? next; // follow-up question (one at a time)
        } else {
          _done = true;
        }
      });
      await _voice.say(_reply ?? '', force: true);
      if (!_done && mounted) _listen();
    } catch (e) {
      if (mounted) toast(context, e, error: true);
      await _voice.say('I could not send your answer. If you need help, press the red help button.', force: true);
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Your medical assistant'), actions: [
        IconButton(tooltip: 'Repeat the question', onPressed: _askAloud, icon: const Icon(Icons.replay)),
      ]),
      body: ListView(padding: const EdgeInsets.all(16), children: [
        Semantics(
          liveRegion: true,
          child: Card(
            color: const Color(0xFFEFF8F8),
            child: Padding(
              padding: const EdgeInsets.all(18),
              child: Row(children: [
                const Icon(Icons.smart_toy, size: 40, color: AppColors.primary),
                const SizedBox(width: 12),
                Expanded(child: Text(_question, style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w700, height: 1.3))),
              ]),
            ),
          ),
        ),
        if (widget.event['simulated'] == true)
          const Padding(
            padding: EdgeInsets.only(top: 8),
            child: Row(children: [
              SourceBadge(simulated: true),
              SizedBox(width: 8),
              Expanded(child: Text('Started by SIMULATED test readings.', style: TextStyle(color: AppColors.simulated))),
            ]),
          ),
        const SizedBox(height: 12),
        if (_listening || _heard.isNotEmpty)
          Semantics(
            liveRegion: true,
            label: _listening ? 'Listening' : 'You said $_heard',
            child: Row(children: [
              Icon(_listening ? Icons.mic : Icons.mic_none, color: _listening ? AppColors.emergency : Colors.black45),
              const SizedBox(width: 8),
              Expanded(child: Text(_listening ? (_heard.isEmpty ? 'Listening… speak now' : _heard) : 'You said: $_heard',
                  style: const TextStyle(fontSize: 16))),
            ]),
          ),
        if (_reply != null) ...[
          const SizedBox(height: 12),
          Card(child: Padding(padding: const EdgeInsets.all(14), child: Text(_reply!, style: const TextStyle(fontSize: 17, height: 1.4)))),
        ],
        const SizedBox(height: 16),
        if (_sending) const LinearProgressIndicator(),
        if (_done)
          FilledButton(onPressed: () => Navigator.pop(context), child: const Text('Close', style: TextStyle(fontSize: 20)))
        else ...[
          if (!_listening && PrefsService.instance.p.voiceAssistant)
            Semantics(
              button: true,
              label: 'Answer by speaking',
              child: OutlinedButton.icon(
                style: OutlinedButton.styleFrom(minimumSize: const Size.fromHeight(60)),
                onPressed: _listen,
                icon: const Icon(Icons.mic, size: 30),
                label: const Text('Speak my answer', style: TextStyle(fontSize: 18)),
              ),
            ),
          const SizedBox(height: 12),
          CommunicationBoard(
            question: _question,
            onAnswer: (a) => _send(a.answer, symptoms: a.symptoms, pain: a.pain,
                text: a.painArea != null ? 'pain area: ${a.painArea}' : ''),
          ),
        ],
        const SizedBox(height: 12),
        const Text('Automatic assistant - not a doctor. If you do not answer, the emergency team will be told.',
            textAlign: TextAlign.center, style: TextStyle(fontSize: 12, color: Colors.black54)),
      ]),
    );
  }
}
