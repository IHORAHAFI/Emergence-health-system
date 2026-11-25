import 'package:flutter/material.dart';

import '../services/api_service.dart';
import '../services/voice_service.dart';
import '../widgets/common.dart';
import '../widgets/communication_board.dart';

/// For patients who cannot speak (and anyone who prefers tapping): choose pictures, the phone says it aloud,
/// and the report goes to the team. "I need emergency help" or "I can't breathe" escalates at once.
class SpeakForMeScreen extends StatefulWidget {
  const SpeakForMeScreen({super.key});
  @override
  State<SpeakForMeScreen> createState() => _SpeakForMeScreenState();
}

class _SpeakForMeScreenState extends State<SpeakForMeScreen> {
  String? _result;
  bool _sending = false;

  Future<void> _send(BoardAnswer a) async {
    if (a.answer == 'ok') {
      setState(() => _result = 'Good. Tap HELP at any time if you need it.');
      return VoiceService.instance.say(_result!, force: true);
    }
    setState(() => _sending = true);
    try {
      final r = Map<String, dynamic>.from(await ApiService.instance.post('/monitoring/events/', {
        'kind': 'patient_report',
        'symptoms': a.symptoms, // HELP adds emergency_help itself; NO alone is an ordinary report
        'text': [if (a.pain != null) 'pain ${a.pain}/10', if (a.painArea != null) 'in ${a.painArea}'].join(' '),
      }));
      final escalated = r['status'] == 'escalated';
      _result = escalated
          ? 'Help is being arranged now. The emergency team has been told and will contact you.'
          : 'Your message was sent to the medical team.';
      await VoiceService.instance.say(_result!, force: true);
    } catch (e) {
      _result = 'Could not send. In danger, call 912.';
      if (mounted) toast(context, e, error: true);
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(title: const Text('Show how you feel')),
        body: ListView(padding: const EdgeInsets.all(16), children: [
          if (_sending) const LinearProgressIndicator(),
          if (_result != null)
            Semantics(
              liveRegion: true,
              child: Card(child: Padding(padding: const EdgeInsets.all(16), child: Text(_result!, style: const TextStyle(fontSize: 20)))),
            ),
          const SizedBox(height: 12),
          CommunicationBoard(onAnswer: _send),
        ]),
      );
}
