import 'package:flutter/material.dart';

import '../notifications_tab.dart';
import 'alerts_tab.dart';
import 'overview_tab.dart';
import 'staff_account_tab.dart';
import 'staff_emergencies_tab.dart';
import 'staff_tests_tab.dart';

/// Operator / admin: Alerts · Emergencies · Tests · Overview · Account
/// Medical staff ("hospital" role): Assigned · Alerts (notifications) · Account
/// Every list comes from role-scoped backend endpoints; this shell only decides which tabs exist.
class StaffShell extends StatefulWidget {
  final String role;
  final String name;
  const StaffShell({super.key, required this.role, this.name = ''});
  @override
  State<StaffShell> createState() => _StaffShellState();
}

class _StaffShellState extends State<StaffShell> {
  int _tab = 0;
  bool get _operator => widget.role == 'operator' || widget.role == 'admin';

  @override
  Widget build(BuildContext context) {
    final tabs = _operator
        ? <(Widget, NavigationDestination)>[
            (const AlertsTab(), const NavigationDestination(icon: Icon(Icons.campaign_outlined), selectedIcon: Icon(Icons.campaign), label: 'Alerts')),
            (const StaffEmergenciesTab(title: 'Emergencies'), const NavigationDestination(icon: Icon(Icons.emergency_outlined), selectedIcon: Icon(Icons.emergency), label: 'Emergencies')),
            (const StaffTestsTab(), const NavigationDestination(icon: Icon(Icons.biotech_outlined), selectedIcon: Icon(Icons.biotech), label: 'Tests')),
            (const OverviewTab(), const NavigationDestination(icon: Icon(Icons.insights_outlined), selectedIcon: Icon(Icons.insights), label: 'Overview')),
            (StaffAccountTab(role: widget.role), const NavigationDestination(icon: Icon(Icons.person_outline), selectedIcon: Icon(Icons.person), label: 'Account')),
          ]
        : <(Widget, NavigationDestination)>[
            (const StaffEmergenciesTab(title: 'Assigned patients'), const NavigationDestination(icon: Icon(Icons.assignment_ind_outlined), selectedIcon: Icon(Icons.assignment_ind), label: 'Assigned')),
            (NotificationsTab(onChanged: () {}), const NavigationDestination(icon: Icon(Icons.notifications_outlined), selectedIcon: Icon(Icons.notifications), label: 'Alerts')),
            (StaffAccountTab(role: widget.role), const NavigationDestination(icon: Icon(Icons.person_outline), selectedIcon: Icon(Icons.person), label: 'Account')),
          ];
    return Scaffold(
      body: SafeArea(child: IndexedStack(index: _tab, children: [for (final t in tabs) t.$1])),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _tab,
        onDestinationSelected: (i) => setState(() => _tab = i),
        destinations: [for (final t in tabs) t.$2],
      ),
    );
  }
}
