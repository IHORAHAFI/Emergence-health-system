import 'package:flutter/material.dart';

import '../../models/emergency.dart';
import '../../services/api_service.dart';
import '../../widgets/common.dart';
import '../../widgets/priority_badge.dart';
import 'staff_emergency_screen.dart';

/// Emergencies the signed-in staff member may see (the backend scopes the list by role:
/// operators all, medical staff only their hospital's assigned patients).
class StaffEmergenciesTab extends StatefulWidget {
  final String title;
  const StaffEmergenciesTab({super.key, required this.title});
  @override
  State<StaffEmergenciesTab> createState() => _StaffEmergenciesTabState();
}

class _StaffEmergenciesTabState extends State<StaffEmergenciesTab> {
  bool _activeOnly = true;
  late Future<List<Emergency>> _items;

  @override
  void initState() {
    super.initState();
    _reload();
  }

  void _reload() => setState(() {
        _items = ApiService.instance.myEmergencies(activeOnly: _activeOnly);
      });

  @override
  Widget build(BuildContext context) {
    return Column(children: [
      Padding(
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
        child: Row(children: [
          Expanded(child: Text(widget.title, style: const TextStyle(fontSize: 26, fontWeight: FontWeight.w800))),
          FilterChip(
            label: const Text('Open only'),
            selected: _activeOnly,
            onSelected: (v) {
              _activeOnly = v;
              _reload();
            },
          ),
        ]),
      ),
      Expanded(
        child: FutureBuilder<List<Emergency>>(
          future: _items,
          builder: (c, s) {
            if (s.hasError) return ErrorRetry(s.error!, _reload);
            if (!s.hasData) return const Center(child: CircularProgressIndicator());
            if (s.data!.isEmpty) return const EmptyState(Icons.inbox_outlined, 'No emergencies');
            return RefreshIndicator(
              onRefresh: () async => _reload(),
              child: ListView.separated(
                padding: const EdgeInsets.all(12),
                itemCount: s.data!.length,
                separatorBuilder: (_, __) => const SizedBox(height: 8),
                itemBuilder: (c, i) {
                  final e = s.data![i];
                  final label = e.operatorLabel;
                  return Card(
                    child: ListTile(
                      contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 6),
                      leading: PriorityBadge(e.priority),
                      title: Text(e.typeDisplay, style: const TextStyle(fontWeight: FontWeight.w700)),
                      subtitle: Text([
                        e.patientName,
                        e.reference,
                        if (e.createdAt != null) ago(e.createdAt!),
                        if (e.escalated) 'AI escalated',
                      ].where((x) => x.isNotEmpty).join(' · ')),
                      trailing: StatusChip(_statusKey(label), label),
                      onTap: () => Navigator.push(
                          context, MaterialPageRoute(builder: (_) => StaffEmergencyScreen(emergencyId: e.id))).then((_) => _reload()),
                    ),
                  );
                },
              ),
            );
          },
        ),
      ),
    ]);
  }
}

/// Maps the operator label to the colour keys used by StatusChip.
String _statusKey(String label) => switch (label) {
      'PENDING' => 'pending',
      'ACCEPTED' => 'accepted',
      'IN PROGRESS' => 'testing',
      'COMPLETED' => 'completed',
      _ => 'cancelled',
    };
