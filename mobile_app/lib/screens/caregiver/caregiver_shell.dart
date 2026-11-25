import 'package:flutter/material.dart';

import '../../services/api_service.dart';
import '../../theme.dart';
import '../../widgets/common.dart';
import '../notifications_tab.dart';
import '../staff/staff_account_tab.dart';
import 'caregiver_patient_screen.dart';

/// Caregiver: the patients who authorised me · my alerts · account.
class CaregiverShell extends StatefulWidget {
  const CaregiverShell({super.key});
  @override
  State<CaregiverShell> createState() => _CaregiverShellState();
}

class _CaregiverShellState extends State<CaregiverShell> {
  int _tab = 0;

  @override
  Widget build(BuildContext context) => Scaffold(
        body: SafeArea(
          child: IndexedStack(index: _tab, children: [
            const _PatientsTab(),
            NotificationsTab(onChanged: () {}),
            const StaffAccountTab(role: 'caregiver'),
          ]),
        ),
        bottomNavigationBar: NavigationBar(
          selectedIndex: _tab,
          onDestinationSelected: (i) => setState(() => _tab = i),
          destinations: const [
            NavigationDestination(icon: Icon(Icons.family_restroom), label: 'My patients'),
            NavigationDestination(icon: Icon(Icons.notifications_outlined), selectedIcon: Icon(Icons.notifications), label: 'Alerts'),
            NavigationDestination(icon: Icon(Icons.person_outline), selectedIcon: Icon(Icons.person), label: 'Account'),
          ],
        ),
      );
}

class _PatientsTab extends StatefulWidget {
  const _PatientsTab();
  @override
  State<_PatientsTab> createState() => _PatientsTabState();
}

class _PatientsTabState extends State<_PatientsTab> {
  late Future<List> _links;
  late Future<List> _events;

  @override
  void initState() {
    super.initState();
    _reload();
  }

  void _reload() => setState(() {
        _links = ApiService.instance.get('/caregivers/').then((v) => v as List);
        _events = ApiService.instance.get('/monitoring/events/', {'open': '1'}).then((v) => v as List);
      });

  Future<void> _accept() async {
    final c = TextEditingController();
    final code = await showDialog<String>(
      context: context,
      builder: (d) => AlertDialog(
        title: const Text('Enter the invitation code'),
        content: TextField(controller: c, textCapitalization: TextCapitalization.characters,
            style: const TextStyle(fontSize: 24, letterSpacing: 3), decoration: const InputDecoration(hintText: 'ABCD1234')),
        actions: [
          TextButton(onPressed: () => Navigator.pop(d), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(d, c.text.trim()), child: const Text('Connect')),
        ],
      ),
    );
    if (code == null || code.isEmpty) return;
    try {
      await ApiService.instance.post('/caregivers/accept/', {'code': code});
      if (mounted) toast(context, 'Connected');
      _reload();
    } catch (e) {
      if (mounted) toast(context, e, error: true);
    }
  }

  @override
  Widget build(BuildContext context) => RefreshIndicator(
        onRefresh: () async => _reload(),
        child: ListView(padding: const EdgeInsets.fromLTRB(16, 12, 16, 24), children: [
          Row(children: [
            const Expanded(child: Text('My patients', style: TextStyle(fontSize: 26, fontWeight: FontWeight.w800))),
            FilledButton.icon(
                style: FilledButton.styleFrom(minimumSize: const Size(0, 44)),
                onPressed: _accept, icon: const Icon(Icons.vpn_key), label: const Text('Add')),
          ]),
          FutureBuilder<List>(
            future: _events,
            builder: (c, s) {
              final open = (s.data ?? []).map((e) => Map<String, dynamic>.from(e)).toList();
              if (open.isEmpty) return const SizedBox.shrink();
              return Card(
                color: const Color(0xFFFEF3F2),
                margin: const EdgeInsets.only(top: 12),
                child: Column(children: [
                  for (final e in open)
                    ListTile(
                      leading: const Icon(Icons.warning_amber, color: AppColors.emergency),
                      title: Text('${e['patient']}: ${e['kind_display']}', style: const TextStyle(fontWeight: FontWeight.w700)),
                      subtitle: Text(e['status_display'] ?? ''),
                      onTap: () => Navigator.push(context,
                              MaterialPageRoute(builder: (_) => CaregiverPatientScreen(patientId: e['patient_id'])))
                          .then((_) => _reload()),
                    ),
                ]),
              );
            },
          ),
          const SizedBox(height: 12),
          FutureBuilder<List>(
            future: _links,
            builder: (c, s) {
              if (s.hasError) return ErrorRetry(s.error!, _reload);
              if (!s.hasData) return const Center(child: CircularProgressIndicator());
              if (s.data!.isEmpty) {
                return const EmptyState(Icons.vpn_key, 'Ask the patient for an invitation code, then press Add.');
              }
              return Column(children: [
                for (final l in s.data!.map((e) => Map<String, dynamic>.from(e)))
                  Padding(
                    padding: const EdgeInsets.only(bottom: 10),
                    child: Card(
                      child: ListTile(
                        contentPadding: const EdgeInsets.all(14),
                        leading: const CircleAvatar(backgroundColor: AppColors.primary, child: Icon(Icons.person, color: Colors.white)),
                        title: Text(l['name'] ?? '', style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 17)),
                        subtitle: Text(l['relationship'] ?? ''),
                        trailing: const Icon(Icons.chevron_right),
                        onTap: () => Navigator.push(context,
                                MaterialPageRoute(builder: (_) => CaregiverPatientScreen(patientId: l['patient_id'])))
                            .then((_) => _reload()),
                      ),
                    ),
                  ),
              ]);
            },
          ),
        ]),
      );
}
