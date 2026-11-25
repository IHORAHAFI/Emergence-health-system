import 'package:flutter/material.dart';
import 'package:flutter/semantics.dart';

import '../services/voice_service.dart';
import '../theme.dart';

/// What the patient chose on the board.
class BoardAnswer {
  final String answer; // ok | not_ok | help
  final List<String> symptoms;
  final int? pain;
  final String? painArea;
  const BoardAnswer(this.answer, {this.symptoms = const [], this.pain, this.painArea});
}

/// Symptom cards (keys understood by the backend). Icons + very short words for people who cannot read well;
/// every card is read aloud when chosen and has a screen-reader label.
const symptomCards = <(String, String, IconData, Color)>[
  ('pain', 'I have pain', Icons.personal_injury, Color(0xFFF79009)),
  ('cannot_breathe', "I can't breathe properly", Icons.air, AppColors.emergency),
  ('dizzy', 'I feel dizzy', Icons.blur_on, Color(0xFF7F56D9)),
  ('weak', 'I feel weak', Icons.battery_1_bar, Color(0xFF667085)),
  ('fever', 'I have a fever', Icons.thermostat, Color(0xFFF04438)),
  ('emergency_help', 'I need emergency help', Icons.emergency, AppColors.emergency),
  ('cannot_speak', "I can't speak", Icons.voice_over_off, Color(0xFF2E90FA)),
  ('cannot_move', "I can't move", Icons.accessible, Color(0xFF2E90FA)),
];

const _painAreas = <(String, String, Alignment)>[
  ('head', 'Head', Alignment(0, -0.92)),
  ('chest', 'Chest', Alignment(0, -0.45)),
  ('belly', 'Belly', Alignment(0, -0.08)),
  ('left_arm', 'Left arm', Alignment(-0.78, -0.35)),
  ('right_arm', 'Right arm', Alignment(0.78, -0.35)),
  ('back', 'Back', Alignment(0.42, -0.2)),
  ('left_leg', 'Left leg', Alignment(-0.3, 0.6)),
  ('right_leg', 'Right leg', Alignment(0.3, 0.6)),
];

/// YES / NO / HELP + symptom cards + pain map. Calls [onAnswer] when the patient confirms.
class CommunicationBoard extends StatefulWidget {
  final void Function(BoardAnswer a) onAnswer;
  final String question;
  const CommunicationBoard({super.key, required this.onAnswer, this.question = 'Are you okay?'});
  @override
  State<CommunicationBoard> createState() => _CommunicationBoardState();
}

class _CommunicationBoardState extends State<CommunicationBoard> {
  final _chosen = <String>{};
  int? _pain;
  String? _area;
  bool _details = false;

  void _speak(String t) {
    VoiceService.instance.say(t, force: true);
    SemanticsService.announce(t, TextDirection.ltr);
  }

  void _send(String answer) {
    final severe = _chosen.contains('emergency_help') || _chosen.contains('cannot_breathe');
    widget.onAnswer(BoardAnswer(severe ? 'help' : answer, symptoms: _chosen.toList(), pain: _pain, painArea: _area));
  }

  Widget _big(String label, String speech, IconData icon, Color color, VoidCallback onTap) => Expanded(
        child: Semantics(
          button: true,
          label: speech,
          child: Material(
            color: color,
            borderRadius: BorderRadius.circular(24),
            child: InkWell(
              borderRadius: BorderRadius.circular(24),
              onTap: () {
                _speak(speech);
                onTap();
              },
              // the visible word is hidden from screen readers: the Semantics label above says it once
              child: ExcludeSemantics(
                child: SizedBox(
                  height: 120,
                  child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
                    Icon(icon, color: Colors.white, size: 48),
                    Text(label, style: const TextStyle(color: Colors.white, fontSize: 22, fontWeight: FontWeight.w900)),
                  ]),
                ),
              ),
            ),
          ),
        ),
      );

  @override
  Widget build(BuildContext context) {
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      Row(children: [
        _big('YES', "Yes, I'm okay", Icons.thumb_up, AppColors.ok, () => _send('ok')),
        const SizedBox(width: 10),
        _big('NO', "No, I'm not okay", Icons.thumb_down, AppColors.warn, () => setState(() => _details = true)),
      ]),
      const SizedBox(height: 10),
      Row(children: [
        _big('HELP', 'I need help now', Icons.emergency, AppColors.emergency, () {
          _chosen.add('emergency_help');
          _send('help');
        }),
      ]),
      if (_details) ...[
        const SizedBox(height: 16),
        const Text('Tap what is wrong', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700)),
        const SizedBox(height: 8),
        GridView.count(
          crossAxisCount: 2,
          shrinkWrap: true,
          physics: const NeverScrollableScrollPhysics(),
          mainAxisSpacing: 8,
          crossAxisSpacing: 8,
          childAspectRatio: 1.6,
          children: [
            for (final c in symptomCards)
              Semantics(
                button: true,
                selected: _chosen.contains(c.$1),
                label: c.$2,
                child: Material(
                  color: _chosen.contains(c.$1) ? c.$4 : Colors.white,
                  borderRadius: BorderRadius.circular(18),
                  child: InkWell(
                    borderRadius: BorderRadius.circular(18),
                    onTap: () {
                      _speak(c.$2);
                      setState(() => _chosen.contains(c.$1) ? _chosen.remove(c.$1) : _chosen.add(c.$1));
                    },
                    child: ExcludeSemantics(child: Container(
                      decoration: BoxDecoration(borderRadius: BorderRadius.circular(18), border: Border.all(color: c.$4, width: 2)),
                      padding: const EdgeInsets.all(8),
                      child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
                        Icon(c.$3, size: 34, color: _chosen.contains(c.$1) ? Colors.white : c.$4),
                        Text(c.$2, textAlign: TextAlign.center,
                            style: TextStyle(fontWeight: FontWeight.w700, color: _chosen.contains(c.$1) ? Colors.white : null)),
                      ]),
                    )),
                  ),
                ),
              ),
          ],
        ),
        if (_chosen.contains('pain')) ...[
          const SizedBox(height: 16),
          const Text('Where is the pain?', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700)),
          _bodyMap(),
          const SizedBox(height: 12),
          const Text('How strong? 0 = none, 10 = worst', style: TextStyle(fontSize: 16, fontWeight: FontWeight.w600)),
          Wrap(spacing: 6, runSpacing: 6, children: [
            for (var i = 0; i <= 10; i++)
              Semantics(
                button: true,
                label: 'Pain $i out of 10',
                child: ChoiceChip(
                  label: Text('$i', style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w800)),
                  selected: _pain == i,
                  selectedColor: Color.lerp(AppColors.ok, AppColors.emergency, i / 10),
                  onSelected: (_) {
                    _speak('Pain $i out of 10');
                    setState(() => _pain = i);
                  },
                ),
              ),
          ]),
        ],
        const SizedBox(height: 16),
        FilledButton.icon(
          style: FilledButton.styleFrom(minimumSize: const Size.fromHeight(64)),
          onPressed: () {
            _speak('Sending your answer');
            _send('not_ok');
          },
          icon: const Icon(Icons.send),
          label: const Text('Send', style: TextStyle(fontSize: 20)),
        ),
      ],
    ]);
  }

  Widget _bodyMap() => AspectRatio(
        aspectRatio: 0.75,
        child: Stack(children: [
          const Center(child: Icon(Icons.accessibility_new, size: 260, color: Color(0xFFD0D5DD))),
          for (final a in _painAreas)
            Align(
              alignment: a.$3,
              child: Semantics(
                button: true,
                selected: _area == a.$1,
                label: 'Pain in ${a.$2}',
                child: GestureDetector(
                  onTap: () {
                    _speak('Pain in the ${a.$2.toLowerCase()}');
                    setState(() => _area = a.$1);
                  },
                  child: ExcludeSemantics(child: Container(
                    padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
                    decoration: BoxDecoration(
                      color: _area == a.$1 ? AppColors.emergency : Colors.white,
                      borderRadius: BorderRadius.circular(20),
                      border: Border.all(color: AppColors.emergency, width: 2),
                    ),
                    child: Text(a.$2, style: TextStyle(fontWeight: FontWeight.w700, color: _area == a.$1 ? Colors.white : null)),
                  )),
                ),
              ),
            ),
        ]),
      );
}
