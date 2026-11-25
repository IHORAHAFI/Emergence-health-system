import 'package:flutter/material.dart';

import '../services/fall_detector.dart';
import '../services/location_service.dart';
import '../services/prefs_service.dart';
import '../services/voice_service.dart';
import '../widgets/common.dart';
import 'caregivers_screen.dart';
import 'home_monitoring_screen.dart';

/// One simple page for every accessibility, voice, monitoring, privacy and caregiver choice.
class AccessibilitySettingsScreen extends StatefulWidget {
  const AccessibilitySettingsScreen({super.key});
  @override
  State<AccessibilitySettingsScreen> createState() => _AccessibilitySettingsScreenState();
}

class _AccessibilitySettingsScreenState extends State<AccessibilitySettingsScreen> {
  final _s = PrefsService.instance;

  @override
  void initState() {
    super.initState();
    _s.syncFromServer().then((_) => mounted ? setState(() {}) : null);
  }

  Future<void> _set(void Function(AppPrefs p) f) async {
    final err = await _s.update(f);
    if (mounted) {
      setState(() {});
      if (err != null) toast(context, 'Saved on this phone only: $err', error: true);
    }
  }

  Widget _switch(String title, String sub, bool v, void Function(bool) on, {IconData? icon}) => SwitchListTile(
        secondary: icon == null ? null : Icon(icon),
        title: Text(title, style: const TextStyle(fontWeight: FontWeight.w600)),
        subtitle: Text(sub),
        value: v,
        onChanged: (x) {
          on(x);
          VoiceService.instance.say('$title ${x ? 'on' : 'off'}');
        },
      );

  Future<void> _pickTime() async {
    final t = await showTimePicker(context: context, initialTime: const TimeOfDay(hour: 8, minute: 0));
    if (t == null) return;
    final v = '${t.hour.toString().padLeft(2, '0')}:${t.minute.toString().padLeft(2, '0')}';
    _set((p) => p.checkinTimes = {...p.checkinTimes, v}.toList()..sort());
  }

  Future<void> _homeMonitoring(bool on) async {
    if (on) {
      final ok = await showDialog<bool>(
        context: context,
        builder: (c) => AlertDialog(
          title: const Text('Switch on home monitoring?'),
          content: const Text('Readings from your own devices are stored and checked automatically. If a reading looks '
              'concerning, the assistant may contact you, and if you need help or do not answer, the emergency team, '
              'medical staff and your caregivers may be told. You can stop at any time.'),
          actions: [
            TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Not now')),
            FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('I agree')),
          ],
        ),
      );
      if (ok != true) return;
    }
    try {
      await _s.setHomeMonitoring(on);
      if (mounted) setState(() {});
    } catch (e) {
      if (mounted) toast(context, e, error: true);
    }
  }

  @override
  Widget build(BuildContext context) {
    final p = _s.p;
    return Scaffold(
      appBar: AppBar(title: const Text('Accessibility & assistant')),
      body: ListView(padding: const EdgeInsets.only(bottom: 32), children: [
        const _Head('Seeing and reading'),
        ListTile(
          leading: const Icon(Icons.text_fields),
          title: const Text('Text size', style: TextStyle(fontWeight: FontWeight.w600)),
          subtitle: Slider(
            value: p.textScale, min: 0.8, max: 2.0, divisions: 12, label: '${(p.textScale * 100).round()}%',
            onChanged: (v) => setState(() => p.textScale = v),
            onChangeEnd: (v) => _set((x) => x.textScale = v),
          ),
        ),
        _switch('High contrast', 'Black and yellow, easier to see', p.highContrast, (v) => _set((x) => x.highContrast = v),
            icon: Icons.contrast),
        _switch('Simple mode', 'Only four big buttons, few words', p.simplified, (v) => _set((x) => x.simplified = v),
            icon: Icons.grid_view),
        const ListTile(
          leading: Icon(Icons.record_voice_over),
          title: Text('Screen reader', style: TextStyle(fontWeight: FontWeight.w600)),
          subtitle: Text('The app works with TalkBack (Android) and VoiceOver (iPhone). Turn them on in your phone settings.'),
        ),
        const _Head('Voice'),
        _switch('Voice assistant', 'Speak instead of typing', p.voiceAssistant, (v) => _set((x) => x.voiceAssistant = v), icon: Icons.mic),
        _switch('Read answers aloud', 'The assistant speaks its answers', p.spokenResponses, (v) => _set((x) => x.spokenResponses = v),
            icon: Icons.volume_up),
        _switch('AI check-ins by voice', 'The assistant may start talking when it is worried and the app is open',
            p.aiVoiceCheckins, (v) => _set((x) => x.aiVoiceCheckins = v), icon: Icons.smart_toy),
        _switch('Voice navigation', 'Say "tests", "results" or "help" to move around', p.voiceNavigation,
            (v) => _set((x) => x.voiceNavigation = v), icon: Icons.assistant_navigation),
        _switch('Hands-free help phrases', 'While the app is open, listens for "help me" or "emergency". '
            'A red microphone shows when it listens. It cannot listen when the app is closed.', p.handsFree,
            (v) => _set((x) => x.handsFree = v), icon: Icons.hearing),
        ListTile(
          leading: const Icon(Icons.speed),
          title: const Text('Voice speed', style: TextStyle(fontWeight: FontWeight.w600)),
          subtitle: Slider(
            value: p.voiceRate, min: 0.2, max: 0.8, divisions: 6,
            onChanged: (v) => setState(() => p.voiceRate = v),
            onChangeEnd: (v) async {
              await _set((x) => x.voiceRate = v);
              VoiceService.instance.say('This is how fast I will speak.', force: true);
            },
          ),
        ),
        ListTile(
          leading: const Icon(Icons.language),
          title: const Text('Language', style: TextStyle(fontWeight: FontWeight.w600)),
          subtitle: const Text('Kinyarwanda speech works only if your phone has a Kinyarwanda voice'),
          trailing: DropdownButton<String>(
            value: p.language,
            items: const [
              DropdownMenuItem(value: 'en', child: Text('English')),
              DropdownMenuItem(value: 'fr', child: Text('Français')),
              DropdownMenuItem(value: 'rw', child: Text('Kinyarwanda')),
            ],
            onChanged: (v) => _set((x) => x.language = v ?? 'en'),
          ),
        ),
        const _Head('Health monitoring'),
        _switch('Home monitoring', 'Your own devices send readings; the assistant checks them', p.homeMonitoring, _homeMonitoring,
            icon: Icons.monitor_heart),
        if (p.homeMonitoring)
          ListTile(
            leading: const Icon(Icons.bluetooth),
            title: const Text('Connect a home device'),
            trailing: const Icon(Icons.chevron_right),
            onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const HomeMonitoringScreen())),
          ),
        _switch('Daily check-ins', 'The assistant asks how you feel at the times below', p.checkinsEnabled,
            (v) => _set((x) => x.checkinsEnabled = v), icon: Icons.schedule),
        if (p.checkinsEnabled)
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: Wrap(spacing: 8, children: [
              for (final t in p.checkinTimes)
                InputChip(label: Text(t), onDeleted: () => _set((x) => x.checkinTimes = [...x.checkinTimes]..remove(t))),
              ActionChip(avatar: const Icon(Icons.add), label: const Text('Add time'), onPressed: _pickTime),
            ]),
          ),
        _switch('Possible-fall detection', 'Uses the phone motion sensor while the app is open. A dropped phone can '
            'also trigger it - you just answer "I\'m okay".', p.fallDetection, (v) async {
          await _set((x) => x.fallDetection = v);
          v ? FallDetector.instance.start() : FallDetector.instance.stop();
        }, icon: Icons.personal_injury),
        const _Head('Privacy and help from others'),
        _switch('Share my location in an automatic emergency',
            'Only the last location the app recorded is used, only for an emergency', p.shareLocation, (v) async {
          if (v) {
            try {
              final pos = await LocationService.current();
              await _set((x) => x.shareLocation = true);
              await LocationService.report(pos);
            } catch (e) {
              if (mounted) toast(context, e, error: true);
            }
          } else {
            await _set((x) => x.shareLocation = false);
          }
        }, icon: Icons.location_on),
        _switch('Tell my caregivers', 'Caregivers you authorised get alerts', p.notifyCaregivers,
            (v) => _set((x) => x.notifyCaregivers = v), icon: Icons.family_restroom),
        ListTile(
          leading: const Icon(Icons.group_add),
          title: const Text('My caregivers', style: TextStyle(fontWeight: FontWeight.w600)),
          subtitle: const Text('Invite someone and choose what they may do'),
          trailing: const Icon(Icons.chevron_right),
          onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const CaregiversScreen())),
        ),
      ]),
    );
  }
}

class _Head extends StatelessWidget {
  final String t;
  const _Head(this.t);
  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.fromLTRB(16, 20, 16, 4),
        child: Semantics(header: true, child: Text(t, style: const TextStyle(fontSize: 17, fontWeight: FontWeight.w800))),
      );
}
