import 'package:flutter/material.dart';

import '../models/test_request.dart';
import '../services/api_service.dart';
import '../theme.dart';
import '../widgets/common.dart';
import 'test_detail_screen.dart';

/// Pick tests with big cards, then submit. Open requests are shown below.
class TestsTab extends StatefulWidget {
  final VoidCallback onRequested;
  const TestsTab({super.key, required this.onRequested});
  @override
  State<TestsTab> createState() => _TestsTabState();
}

class _TestsTabState extends State<TestsTab> {
  final _selected = <String>{};
  bool _sending = false;
  List<TestRequest> _open = [];

  bool get _all => _selected.length == vitals.length;

  @override
  void initState() {
    super.initState();
    _loadOpen();
  }

  Future<void> _loadOpen() async {
    try {
      final o = await ApiService.instance.myTests(openOnly: true);
      if (mounted) setState(() => _open = o);
    } catch (_) {}
  }

  /// Tests already in an open request cannot be requested again (the backend enforces this too).
  Set<String> get _busy => {for (final r in _open) ...r.tests};

  Future<void> _submit() async {
    setState(() => _sending = true);
    try {
      final r = await ApiService.instance.requestTests(_selected.toList());
      if (!mounted) return;
      setState(_selected.clear);
      toast(context, 'Request ${r.reference} sent');
      await _loadOpen();
      if (!mounted) return;
      Navigator.push(context, MaterialPageRoute(builder: (_) => TestDetailScreen(reference: r.reference)))
          .then((_) => _loadOpen());
    } catch (e) {
      if (mounted) toast(context, e, error: true);
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final busy = _busy;
    return RefreshIndicator(
      onRefresh: _loadOpen,
      child: ListView(padding: const EdgeInsets.fromLTRB(16, 12, 16, 24), children: [
        const Text('Medical tests', style: TextStyle(fontSize: 26, fontWeight: FontWeight.w800)),
        const Text('Tap the tests you need', style: TextStyle(color: Colors.black54)),
        const SizedBox(height: 16),
        _completeCard(busy),
        const SizedBox(height: 12),
        GridView.count(
          crossAxisCount: 2,
          shrinkWrap: true,
          physics: const NeverScrollableScrollPhysics(),
          mainAxisSpacing: 12,
          crossAxisSpacing: 12,
          childAspectRatio: 1.15,
          children: [for (final code in vitals.keys) _testCard(code, busy.contains(code))],
        ),
        const SizedBox(height: 20),
        FilledButton.icon(
          onPressed: _selected.isEmpty || _sending ? null : _submit,
          icon: _sending
              ? const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
              : const Icon(Icons.send),
          label: Text(_selected.isEmpty ? 'Select a test' : 'Request ${_all ? 'all vital signs' : '${_selected.length} test${_selected.length > 1 ? 's' : ''}'}'),
        ),
        if (_open.isNotEmpty) ...[
          const SectionTitle('In progress'),
          for (final r in _open)
            Padding(
              padding: const EdgeInsets.only(bottom: 10),
              child: Card(
                child: ListTile(
                  leading: Icon(r.isTesting ? Icons.sensors : Icons.hourglass_top, color: statusColor(r.status)),
                  title: Text(r.testNames, maxLines: 1, overflow: TextOverflow.ellipsis),
                  subtitle: Text(r.reference),
                  trailing: StatusChip(r.status, r.statusDisplay),
                  onTap: () => Navigator.push(
                          context, MaterialPageRoute(builder: (_) => TestDetailScreen(reference: r.reference)))
                      .then((_) => _loadOpen()),
                ),
              ),
            ),
        ],
      ]),
    );
  }

  Widget _completeCard(Set<String> busy) {
    final available = vitals.keys.where((c) => !busy.contains(c)).toSet();
    final on = available.isNotEmpty && _selected.containsAll(available) && busy.isEmpty;
    return Material(
      color: on ? AppColors.primary : Colors.white,
      borderRadius: BorderRadius.circular(20),
      child: InkWell(
        borderRadius: BorderRadius.circular(20),
        onTap: busy.isNotEmpty
            ? null
            : () => setState(() => on ? _selected.clear() : _selected.addAll(vitals.keys)),
        child: Container(
          padding: const EdgeInsets.all(18),
          decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(20),
              border: Border.all(color: on ? AppColors.primary : const Color(0xFFE4E7EC))),
          child: Row(children: [
            Icon(Icons.health_and_safety, size: 36, color: on ? Colors.white : AppColors.primary),
            const SizedBox(width: 14),
            Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text('Complete vital signs',
                    style: TextStyle(fontSize: 17, fontWeight: FontWeight.w700, color: on ? Colors.white : null)),
                Text(busy.isEmpty ? 'All 5 measurements' : 'Some tests are already in progress',
                    style: TextStyle(color: on ? Colors.white70 : Colors.black54)),
              ]),
            ),
            Icon(on ? Icons.check_circle : Icons.circle_outlined, color: on ? Colors.white : Colors.black26),
          ]),
        ),
      ),
    );
  }

  Widget _testCard(String code, bool busy) {
    final m = vitals[code]!;
    final on = _selected.contains(code);
    return Material(
      color: on ? m.color.withValues(alpha: .12) : Colors.white,
      borderRadius: BorderRadius.circular(20),
      child: InkWell(
        borderRadius: BorderRadius.circular(20),
        onTap: busy ? null : () => setState(() => on ? _selected.remove(code) : _selected.add(code)),
        child: Container(
          padding: const EdgeInsets.all(14),
          decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(20),
              border: Border.all(color: on ? m.color : const Color(0xFFE4E7EC), width: on ? 2 : 1)),
          child: Opacity(
            opacity: busy ? .45 : 1,
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Row(children: [
                CircleAvatar(radius: 22, backgroundColor: m.color.withValues(alpha: .15), child: Icon(m.icon, color: m.color)),
                const Spacer(),
                Icon(on ? Icons.check_circle : Icons.circle_outlined, color: on ? m.color : Colors.black26),
              ]),
              const Spacer(),
              Text(m.label, style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 15)),
              Text(busy ? 'In progress' : m.unit, style: const TextStyle(color: Colors.black54, fontSize: 12)),
            ]),
          ),
        ),
      ),
    );
  }
}
