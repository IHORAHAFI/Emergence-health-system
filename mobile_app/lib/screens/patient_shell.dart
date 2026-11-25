import 'dart:async';

import 'package:flutter/material.dart';

import '../services/api_service.dart';
import '../services/event_watcher.dart';
import '../services/fall_detector.dart';
import '../services/local_notify.dart';
import '../services/prefs_service.dart';
import '../services/voice_service.dart';
import '../theme.dart';
import 'checkin_screen.dart';
import 'home_tab.dart';
import 'notifications_tab.dart';
import 'profile_tab.dart';
import 'results_tab.dart';
import 'simple_home.dart';
import 'tests_tab.dart';
import 'voice_assistant_screen.dart';

/// Patient app: Home · Tests · Results · Alerts · Profile (or the 4-button simple mode).
/// Also runs the proactive helpers while the app is open:
///   * EventWatcher - brings the AI's questions (concerning readings, check-ins, falls) to the patient;
///   * FallDetector - possible-fall detection, only if the patient switched it on;
///   * hands-free help phrases - only if switched on, only while the app is on screen, with a visible indicator.
class PatientShell extends StatefulWidget {
  const PatientShell({super.key});
  @override
  State<PatientShell> createState() => _PatientShellState();
}

class _PatientShellState extends State<PatientShell> with WidgetsBindingObserver {
  int _tab = 0;
  int _unread = 0;
  Timer? _poll;
  final _resultsKey = GlobalKey<ResultsTabState>();
  bool _checkInOpen = false, _foreground = true, _handsFreeRunning = false, _away = false;

  AppPrefs get _p => PrefsService.instance.p;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _checkUnread();
    _poll = Timer.periodic(const Duration(seconds: 20), (_) => _checkUnread());
    PrefsService.instance.syncFromServer().then((_) => _applyPrefs());
    PrefsService.instance.addListener(_applyPrefs);
    EventWatcher.instance.onAsk = _openCheckIn;
    EventWatcher.instance.start();
    LocalNotify.instance.onTap = (payload) async {
      final id = int.tryParse((payload ?? '').replaceFirst('event:', ''));
      if (id == null) return;
      final list = await ApiService.instance.get('/monitoring/events/', {'open': '1'}) as List;
      final e = list.map((x) => Map<String, dynamic>.from(x)).where((x) => x['id'] == id).firstOrNull;
      if (e != null) _openCheckIn(e);
    };
    _applyPrefs();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    PrefsService.instance.removeListener(_applyPrefs);
    _poll?.cancel();
    EventWatcher.instance.stop();
    FallDetector.instance.stop();
    _handsFreeRunning = false;
    VoiceService.instance.stop();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    _foreground = state == AppLifecycleState.resumed;
    if (!_foreground) {
      VoiceService.instance.stop(); // never listen in the background
    } else {
      _applyPrefs();
    }
  }

  void _applyPrefs() {
    _p.fallDetection ? FallDetector.instance.start() : FallDetector.instance.stop();
    if (_p.handsFree && !_handsFreeRunning) _handsFreeLoop();
    if (mounted) setState(() {});
  }

  /// Listens in short sessions for help phrases while the app is on this screen. Uses on-device recognition when
  /// the phone supports it; nothing is recorded, and only a matched phrase leads to any action.
  Future<void> _handsFreeLoop() async {
    _handsFreeRunning = true;
    while (mounted && _p.handsFree) {
      if (!_foreground || _checkInOpen || _away) {
        await Future.delayed(const Duration(seconds: 2));
        continue;
      }
      final heard = await VoiceService.instance.listenOnce(max: const Duration(seconds: 8));
      if (!mounted || !_p.handsFree) break;
      final q = heard.toLowerCase();
      if (VoiceIntents.isEmergency(q) || q.contains('assistant') || q.contains('help')) {
        _away = true;
        await Navigator.push(context, MaterialPageRoute(builder: (_) => const VoiceAssistantScreen()));
        _away = false;
      } else if (_p.voiceNavigation && q.isNotEmpty) {
        _voiceNav(q);
      }
      await Future.delayed(const Duration(milliseconds: 400));
    }
    _handsFreeRunning = false;
    if (mounted) setState(() {});
  }

  void _voiceNav(String q) {
    final map = {'home': 0, 'test': 1, 'result': 2, 'alert': 3, 'notification': 3, 'profile': 4};
    for (final e in map.entries) {
      if (q.contains(e.key)) {
        go(e.value);
        VoiceService.instance.say('Opening ${e.key}s');
        return;
      }
    }
  }

  Future<void> _openCheckIn(Map<String, dynamic> e) async {
    if (_checkInOpen || !mounted) return;
    _checkInOpen = true;
    await VoiceService.instance.stop();
    if (!mounted) {
      _checkInOpen = false;
      return;
    }
    await Navigator.push(context, MaterialPageRoute(builder: (_) => CheckInScreen(event: e)));
    _checkInOpen = false;
  }

  Future<void> _checkUnread() async {
    try {
      final n = await ApiService.instance.notifications(unreadOnly: true);
      if (mounted) setState(() => _unread = n.length);
    } catch (_) {}
  }

  void go(int i) {
    setState(() => _tab = i);
    if (i == 2) _resultsKey.currentState?.reload();
    if (i == 3) _checkUnread();
  }

  Widget _listeningBar() => Semantics(
        liveRegion: true,
        label: 'Microphone on. Say help me, or emergency.',
        child: Container(
          width: double.infinity,
          color: AppColors.emergency,
          padding: const EdgeInsets.symmetric(vertical: 6, horizontal: 12),
          child: Row(children: [
            const Icon(Icons.mic, color: Colors.white, size: 18),
            const SizedBox(width: 6),
            const Expanded(child: Text('Listening for "help me" (app open only)', style: TextStyle(color: Colors.white))),
            InkWell(
              onTap: () => PrefsService.instance.update((p) => p.handsFree = false),
              child: const Text('Turn off', style: TextStyle(color: Colors.white, decoration: TextDecoration.underline)),
            ),
          ]),
        ),
      );

  @override
  Widget build(BuildContext context) {
    final listeningBar = _p.handsFree ? _listeningBar() : const SizedBox.shrink();
    if (_p.simplified) {
      return Scaffold(body: SafeArea(child: Column(children: [listeningBar, Expanded(child: SimpleHome(onGo: go))])));
    }
    final pages = [
      HomeTab(onGo: go),
      TestsTab(onRequested: () => go(2)),
      ResultsTab(key: _resultsKey),
      NotificationsTab(onChanged: _checkUnread),
      const ProfileTab(),
    ];
    return Scaffold(
      body: SafeArea(child: Column(children: [listeningBar, Expanded(child: IndexedStack(index: _tab, children: pages))])),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _tab,
        onDestinationSelected: go,
        destinations: [
          const NavigationDestination(icon: Icon(Icons.home_outlined), selectedIcon: Icon(Icons.home), label: 'Home'),
          const NavigationDestination(
              icon: Icon(Icons.biotech_outlined), selectedIcon: Icon(Icons.biotech), label: 'Tests'),
          const NavigationDestination(
              icon: Icon(Icons.monitor_heart_outlined), selectedIcon: Icon(Icons.monitor_heart), label: 'Results'),
          NavigationDestination(
              icon: Badge(
                  isLabelVisible: _unread > 0, label: Text('$_unread'), child: const Icon(Icons.notifications_outlined)),
              selectedIcon: const Icon(Icons.notifications),
              label: 'Alerts'),
          const NavigationDestination(icon: Icon(Icons.person_outline), selectedIcon: Icon(Icons.person), label: 'Profile'),
        ],
      ),
    );
  }
}
