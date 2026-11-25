import 'package:flutter/material.dart';

import '../models/emergency.dart';
import '../services/api_service.dart';
import '../widgets/common.dart';
import '../widgets/priority_badge.dart';
import 'tracking_screen.dart';

class EmergencyHistoryScreen extends StatefulWidget {
  const EmergencyHistoryScreen({super.key});
  @override
  State<EmergencyHistoryScreen> createState() => _EmergencyHistoryScreenState();
}

class _EmergencyHistoryScreenState extends State<EmergencyHistoryScreen> {
  late Future<List<Emergency>> _items;

  @override
  void initState() {
    super.initState();
    _reload();
  }

  void _reload() => setState(() {
        _items = ApiService.instance.myEmergencies();
      });

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('My emergency requests')),
      body: FutureBuilder<List<Emergency>>(
        future: _items,
        builder: (c, s) {
          if (s.hasError) return ErrorRetry(s.error!, _reload);
          if (!s.hasData) return const Center(child: CircularProgressIndicator());
          if (s.data!.isEmpty) return const EmptyState(Icons.emergency_outlined, 'No emergency requests yet');
          return RefreshIndicator(
            onRefresh: () async => _reload(),
            child: ListView.separated(
              padding: const EdgeInsets.all(16),
              itemCount: s.data!.length,
              separatorBuilder: (_, __) => const SizedBox(height: 10),
              itemBuilder: (c, i) {
                final e = s.data![i];
                return Card(
                  child: ListTile(
                    contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
                    leading: PriorityBadge(e.priority),
                    title: Text(e.typeDisplay, style: const TextStyle(fontWeight: FontWeight.w700)),
                    subtitle: Text([e.reference, if (e.createdAt != null) ago(e.createdAt!)].join(' · ')),
                    trailing: StatusChip(e.status, e.statusDisplay),
                    onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => TrackingScreen(emergencyId: e.id)))
                        .then((_) => _reload()),
                  ),
                );
              },
            ),
          );
        },
      ),
    );
  }
}
