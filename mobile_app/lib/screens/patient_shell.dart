import 'dart:async';

import 'package:flutter/material.dart';

import '../services/api_service.dart';
import 'home_tab.dart';
import 'notifications_tab.dart';
import 'profile_tab.dart';
import 'results_tab.dart';
import 'tests_tab.dart';

/// Patient app: Home · Tests · Results · Alerts · Profile.
class PatientShell extends StatefulWidget {
  const PatientShell({super.key});
  @override
  State<PatientShell> createState() => _PatientShellState();
}

class _PatientShellState extends State<PatientShell> {
  int _tab = 0;
  int _unread = 0;
  Timer? _poll;
  final _resultsKey = GlobalKey<ResultsTabState>();

  @override
  void initState() {
    super.initState();
    _checkUnread();
    _poll = Timer.periodic(const Duration(seconds: 20), (_) => _checkUnread());
  }

  @override
  void dispose() {
    _poll?.cancel();
    super.dispose();
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

  @override
  Widget build(BuildContext context) {
    final pages = [
      HomeTab(onGo: go),
      TestsTab(onRequested: () => go(2)),
      ResultsTab(key: _resultsKey),
      NotificationsTab(onChanged: _checkUnread),
      const ProfileTab(),
    ];
    return Scaffold(
      body: SafeArea(child: IndexedStack(index: _tab, children: pages)),
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
