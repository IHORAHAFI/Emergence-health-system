import 'package:flutter/material.dart';

import '../services/api_service.dart';
import '../theme.dart';
import '../widgets/common.dart';
import 'emergency_history_screen.dart';
import 'test_detail_screen.dart';

class NotificationsTab extends StatefulWidget {
  final VoidCallback onChanged;
  const NotificationsTab({super.key, required this.onChanged});
  @override
  State<NotificationsTab> createState() => _NotificationsTabState();
}

class _NotificationsTabState extends State<NotificationsTab> {
  late Future<List<Map<String, dynamic>>> _items;

  @override
  void initState() {
    super.initState();
    _reload();
  }

  void _reload() => setState(() {
        _items = ApiService.instance.notifications();
      });

  Future<void> _open(Map<String, dynamic> n) async {
    if (n['is_read'] != true) {
      try {
        await ApiService.instance.markRead(n['id']);
      } catch (_) {}
      widget.onChanged();
    }
    if (!mounted) return;
    final testRef = n['test_request_ref'];
    final Widget? target = testRef != null
        ? TestDetailScreen(reference: testRef)
        : n['emergency'] != null
            ? const EmergencyHistoryScreen()
            : null;
    if (target != null) await Navigator.push(context, MaterialPageRoute(builder: (_) => target));
    _reload();
  }

  @override
  Widget build(BuildContext context) {
    return Column(children: [
      Padding(
        padding: const EdgeInsets.fromLTRB(16, 12, 8, 4),
        child: Row(children: [
          const Expanded(child: Text('Alerts', style: TextStyle(fontSize: 26, fontWeight: FontWeight.w800))),
          TextButton.icon(
            icon: const Icon(Icons.done_all),
            label: const Text('Read all'),
            onPressed: () async {
              await ApiService.instance.markAllRead();
              widget.onChanged();
              _reload();
            },
          ),
        ]),
      ),
      Expanded(
        child: FutureBuilder<List<Map<String, dynamic>>>(
          future: _items,
          builder: (c, s) {
            if (s.hasError) return ErrorRetry(s.error!, _reload);
            if (!s.hasData) return const Center(child: CircularProgressIndicator());
            return RefreshIndicator(
              onRefresh: () async => _reload(),
              child: s.data!.isEmpty
                  ? ListView(children: const [EmptyState(Icons.notifications_none, 'No notifications')])
                  : ListView.separated(
                      padding: const EdgeInsets.all(16),
                      itemCount: s.data!.length,
                      separatorBuilder: (_, __) => const SizedBox(height: 10),
                      itemBuilder: (c, i) {
                        final n = s.data![i];
                        final unread = n['is_read'] != true;
                        final isEmergency = n['emergency'] != null && n['test_request_ref'] == null;
                        final created = DateTime.tryParse(n['created_at'] ?? '')?.toLocal();
                        return Card(
                          color: unread ? const Color(0xFFEFF8F8) : Colors.white,
                          child: ListTile(
                            contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
                            leading: CircleAvatar(
                              backgroundColor: (isEmergency ? AppColors.emergency : AppColors.primary).withValues(alpha: .12),
                              child: Icon(isEmergency ? Icons.emergency : Icons.biotech,
                                  color: isEmergency ? AppColors.emergency : AppColors.primary),
                            ),
                            title: Text(n['title'] ?? '',
                                style: TextStyle(fontWeight: unread ? FontWeight.w800 : FontWeight.w500)),
                            subtitle: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                              Text(n['message'] ?? '', maxLines: 3, overflow: TextOverflow.ellipsis),
                              if (created != null)
                                Text(ago(created), style: const TextStyle(fontSize: 11, color: Colors.black45)),
                            ]),
                            onTap: () => _open(n),
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
