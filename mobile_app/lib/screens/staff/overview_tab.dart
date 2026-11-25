import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../services/api_service.dart';
import '../../theme.dart';
import '../../widgets/common.dart';

/// Basic statistics for operators and admins. User and role management stays in Django admin (link below).
class OverviewTab extends StatefulWidget {
  const OverviewTab({super.key});
  @override
  State<OverviewTab> createState() => _OverviewTabState();
}

class _OverviewTabState extends State<OverviewTab> {
  late Future<Map<String, dynamic>> _data;

  @override
  void initState() {
    super.initState();
    _reload();
  }

  void _reload() => setState(() {
        _data = ApiService.instance.staffStats();
      });

  Widget _tile(String label, Object? value, IconData icon, Color color) => Card(
        child: Padding(
          padding: const EdgeInsets.all(14),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Icon(icon, color: color),
            const Spacer(),
            Text('${value ?? 0}', style: const TextStyle(fontSize: 26, fontWeight: FontWeight.w800)),
            Text(label, style: const TextStyle(fontSize: 12, color: Colors.black54)),
          ]),
        ),
      );

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<Map<String, dynamic>>(
      future: _data,
      builder: (c, s) {
        if (s.hasError) return ErrorRetry(s.error!, _reload);
        if (!s.hasData) return const Center(child: CircularProgressIndicator());
        final d = s.data!;
        Map<String, dynamic> m(String k) => Map<String, dynamic>.from(d[k] ?? {});
        final em = m('emergencies_by_status'), tests = m('tests_by_status'), roles = m('users_by_role'), dev = m('devices');
        final open = em.entries.where((e) => e.key != 'completed' && e.key != 'cancelled').fold<int>(0, (a, e) => a + (e.value as int));
        return RefreshIndicator(
          onRefresh: () async => _reload(),
          child: ListView(padding: const EdgeInsets.fromLTRB(16, 12, 16, 24), children: [
            const Text('Overview', style: TextStyle(fontSize: 26, fontWeight: FontWeight.w800)),
            const SizedBox(height: 12),
            GridView.count(
              crossAxisCount: 2, shrinkWrap: true, physics: const NeverScrollableScrollPhysics(),
              mainAxisSpacing: 10, crossAxisSpacing: 10, childAspectRatio: 1.5,
              children: [
                _tile('Unaccepted emergencies', d['unaccepted_open'], Icons.campaign, AppColors.emergency),
                _tile('AI-escalated, still open', d['escalated_open'], Icons.smart_toy, Colors.black87),
                _tile('Open emergencies', open, Icons.emergency, AppColors.warn),
                _tile('Completed emergencies', em['completed'], Icons.verified, AppColors.ok),
                _tile('Pending test requests', tests['pending'], Icons.biotech, AppColors.primary),
                _tile('Tests running', tests['testing'], Icons.sensors, AppColors.primary),
                _tile('Real devices', dev['real'], Icons.bluetooth_connected, AppColors.ok),
                _tile('Simulated devices', dev['simulated'], Icons.science_outlined, AppColors.simulated),
                _tile('Failed notifications', d['notifications_failed'], Icons.sms_failed, AppColors.warn),
                _tile('Staff awaiting approval', d['staff_awaiting_approval'], Icons.how_to_reg, AppColors.warn),
              ],
            ),
            const SectionTitle('Users'),
            Card(
              child: Column(children: [
                for (final e in roles.entries)
                  ListTile(dense: true, title: Text(e.key[0].toUpperCase() + e.key.substring(1)), trailing: Text('${e.value}')),
              ]),
            ),
            const SizedBox(height: 12),
            OutlinedButton.icon(
              onPressed: () => launchUrl(Uri.parse('${ApiService.serverRoot}${d['admin_url'] ?? '/admin/'}'),
                  mode: LaunchMode.externalApplication),
              icon: const Icon(Icons.admin_panel_settings),
              label: const Text('Manage users and roles (web admin)'),
            ),
          ]),
        );
      },
    );
  }
}
