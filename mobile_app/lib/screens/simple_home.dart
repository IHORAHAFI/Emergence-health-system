import 'package:flutter/material.dart';

import '../services/voice_service.dart';
import '../theme.dart';
import 'accessibility_settings_screen.dart';
import 'emergency_request_screen.dart';
import 'speak_for_me_screen.dart';
import 'voice_assistant_screen.dart';

/// Simple mode: four huge buttons, almost no text. Every button says what it does when touched
/// (long-press = hear it without opening), and has a screen-reader label.
class SimpleHome extends StatelessWidget {
  final void Function(int tab) onGo;
  const SimpleHome({super.key, required this.onGo});

  @override
  Widget build(BuildContext context) {
    void open(Widget w) => Navigator.push(context, MaterialPageRoute(builder: (_) => w));
    final items = <(String, String, IconData, Color, VoidCallback)>[
      ('HELP', 'Get emergency help', Icons.emergency, AppColors.emergency, () => open(const EmergencyRequestScreen())),
      ('TALK', 'Talk to your medical assistant', Icons.mic, AppColors.primary, () => open(const VoiceAssistantScreen())),
      ('SHOW', 'I cannot speak. Show symptom pictures', Icons.touch_app, const Color(0xFF7F56D9),
          () => open(const SpeakForMeScreen())),
      ('TESTS', 'Medical tests and results', Icons.monitor_heart, AppColors.ok, () => onGo(2)),
    ];
    return Column(children: [
      Align(
        alignment: Alignment.centerRight,
        child: IconButton(
          tooltip: 'Settings',
          iconSize: 32,
          onPressed: () => open(const AccessibilitySettingsScreen()),
          icon: const Icon(Icons.settings),
        ),
      ),
      Expanded(
        child: GridView.count(
          crossAxisCount: 2,
          padding: const EdgeInsets.all(12),
          mainAxisSpacing: 12,
          crossAxisSpacing: 12,
          children: [
            for (final it in items)
              Semantics(
                button: true,
                label: it.$2,
                child: Material(
                  color: it.$4,
                  borderRadius: BorderRadius.circular(28),
                  child: InkWell(
                    borderRadius: BorderRadius.circular(28),
                    onLongPress: () => VoiceService.instance.say(it.$2, force: true),
                    onTap: () {
                      VoiceService.instance.say(it.$2, force: true);
                      it.$5();
                    },
                    child: ExcludeSemantics(child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
                      Icon(it.$3, size: 72, color: Colors.white),
                      const SizedBox(height: 8),
                      Text(it.$1, style: const TextStyle(color: Colors.white, fontSize: 26, fontWeight: FontWeight.w900)),
                    ])),
                  ),
                ),
              ),
          ],
        ),
      ),
    ]);
  }
}
