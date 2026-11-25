import 'package:flutter/material.dart';

import '../../services/api_service.dart';
import '../../theme.dart';
import '../../widgets/common.dart';

/// Operator test-request workflow (same rules as the web dashboard):
/// accept -> start (virtual IoT devices; values are labelled SIMULATED) -> complete once a reading exists.
class StaffTestsTab extends StatefulWidget {
  final bool standalone;
  const StaffTestsTab({super.key, this.standalone = false});
  @override
  State<StaffTestsTab> createState() => _StaffTestsTabState();
}

class _StaffTestsTabState extends State<StaffTestsTab> {
  late Future<List<Map<String, dynamic>>> _items;
  String? _busy;

  @override
  void initState() {
    super.initState();
    _reload();
  }

  void _reload() => setState(() {
        _items = ApiService.instance.staffTests();
      });

  Future<void> _act(String ref, String action) async {
    if (action == 'start') {
      final ok = await showDialog<bool>(
        context: context,
        builder: (c) => AlertDialog(
          icon: const Icon(Icons.science_outlined, color: AppColors.simulated),
          title: const Text('Start with virtual devices?'),
          content: const Text('The test will run on virtual IoT devices. Every value is labelled SIMULATED and is not '
              'a real measurement. The patient can still connect their own Bluetooth device for REAL readings.'),
          actions: [
            TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Back')),
            FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('Start')),
          ],
        ),
      );
      if (ok != true) return;
    }
    setState(() => _busy = ref);
    try {
      await ApiService.instance.testAction(ref, action);
      if (mounted) toast(context, 'Done');
    } catch (e) {
      if (mounted) toast(context, e, error: true);
    } finally {
      if (mounted) setState(() => _busy = null);
      _reload();
    }
  }

  @override
  Widget build(BuildContext context) {
    final list = FutureBuilder<List<Map<String, dynamic>>>(
      future: _items,
      builder: (c, s) {
        if (s.hasError) return ErrorRetry(s.error!, _reload);
        if (!s.hasData) return const Center(child: CircularProgressIndicator());
        if (s.data!.isEmpty) return const EmptyState(Icons.biotech_outlined, 'No open test requests');
        return RefreshIndicator(
          onRefresh: () async => _reload(),
          child: ListView.separated(
            padding: const EdgeInsets.all(12),
            itemCount: s.data!.length,
            separatorBuilder: (_, __) => const SizedBox(height: 8),
            itemBuilder: (c, i) {
              final r = s.data![i];
              final st = r['status'] as String;
              final actions = <(String, String)>[
                if (st == 'pending') ('accept', 'Accept'),
                if (st == 'accepted') ('start', 'Start test'),
                if (st == 'testing' || st == 'accepted') ('complete', 'Complete'),
                if (st != 'testing') ('cancel', 'Cancel'),
              ];
              final summary = Map<String, dynamic>.from(r['summary'] ?? {});
              return Card(
                child: Padding(
                  padding: const EdgeInsets.all(14),
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Row(children: [
                      Expanded(child: Text(r['test_names'] ?? '', style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 16))),
                      StatusChip(st, (r['status_display'] ?? st).toString().split(' - ').first),
                    ]),
                    Text('${r['patient']} · ${r['patient_code']} · ${r['reference']}', style: const TextStyle(color: Colors.black54)),
                    if (summary.isNotEmpty)
                      Padding(
                        padding: const EdgeInsets.only(top: 6),
                        child: Wrap(spacing: 6, runSpacing: 6, children: [
                          for (final v in summary.values)
                            Chip(
                              avatar: Icon(v['simulated'] == true ? Icons.science_outlined : Icons.bluetooth_connected, size: 16,
                                  color: v['simulated'] == true ? AppColors.simulated : AppColors.ok),
                              label: Text('${v['label']}: ${v['display']}'),
                              visualDensity: VisualDensity.compact,
                            ),
                        ]),
                      ),
                    const SizedBox(height: 8),
                    _busy == r['reference']
                        ? const LinearProgressIndicator()
                        : Wrap(spacing: 8, runSpacing: 8, children: [
                            for (final a in actions)
                              a.$1 == 'cancel'
                                  ? TextButton(onPressed: () => _act(r['reference'], a.$1), child: Text(a.$2))
                                  : FilledButton(
                                      style: FilledButton.styleFrom(minimumSize: const Size(110, 44)),
                                      onPressed: () => _act(r['reference'], a.$1),
                                      child: Text(a.$2)),
                          ]),
                  ]),
                ),
              );
            },
          ),
        );
      },
    );
    if (widget.standalone) return Scaffold(appBar: AppBar(title: const Text('Test requests')), body: list);
    return Column(children: [
      const Padding(
        padding: EdgeInsets.fromLTRB(16, 12, 16, 4),
        child: Align(alignment: Alignment.centerLeft, child: Text('Test requests', style: TextStyle(fontSize: 26, fontWeight: FontWeight.w800))),
      ),
      Expanded(child: list),
    ]);
  }
}
