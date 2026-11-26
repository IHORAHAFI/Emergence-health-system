import 'dart:async';

import 'package:flutter/material.dart';

import '../../services/api_service.dart';
import '../../services/siren_service.dart';
import '../../theme.dart';
import '../../widgets/common.dart';
import '../../widgets/priority_badge.dart';
import 'staff_emergency_screen.dart';
import 'staff_tests_tab.dart';

/// Unhandled patient requests (operators). Polls the backend; the backend advances each request's own
/// 2-minute alert counter and runs the AI escalation, so nothing here depends on the app staying open.
class AlertsTab extends StatefulWidget {
  const AlertsTab({super.key});
  @override
  State<AlertsTab> createState() => _AlertsTabState();
}

class _AlertsTabState extends State<AlertsTab> {
  List<Map<String, dynamic>> _alerts = [];
  List<Map<String, dynamic>> _events = []; // proactive monitoring events (AI summaries)
  String _siren = '', _sirenProblem = '';
  Object? _error;
  bool _loaded = false;
  Timer? _poll, _clock;
  final _sirenSvc = SirenService.instance;

  @override
  void initState() {
    super.initState();
    _sirenSvc.load().then((_) => mounted ? setState(() {}) : null);
    _load();
    _poll = Timer.periodic(const Duration(seconds: 5), (_) => _load());
    _clock = Timer.periodic(const Duration(seconds: 1), (_) => mounted && _alerts.isNotEmpty ? setState(() {}) : null);
  }

  @override
  void dispose() {
    _poll?.cancel();
    _clock?.cancel();
    _sirenSvc.stop();
    super.dispose();
  }

  Future<void> _load([Map<String, dynamic>? data]) async {
    try {
      final d = data ?? await ApiService.instance.staffAlerts();
      final list = ((d['alerts'] ?? []) as List).map((e) => Map<String, dynamic>.from(e)).toList();
      List<Map<String, dynamic>> evs = _events;
      try {
        evs = ((await ApiService.instance.get('/staff/events/', {'open': '1'})) as List)
            .map((e) => Map<String, dynamic>.from(e)).toList();
      } catch (_) {}
      if (!mounted) return;
      setState(() {
        _alerts = list;
        _events = evs;
        _siren = (d['siren_url'] ?? '').toString();
        _sirenProblem = (d['siren_problem'] ?? '').toString();
        _error = null;
        _loaded = true;
      });
      await _sirenSvc.onFeed(list, _siren);
    } catch (e) {
      if (mounted) setState(() => _error = e);
    }
  }

  Future<void> _accept(String key) async {
    try {
      await _load(await ApiService.instance.acceptAlert(key));
      if (mounted) toast(context, 'Accepted - you are now responsible for this request');
    } catch (e) {
      if (mounted) toast(context, e, error: true);
    }
  }

  void _open(Map<String, dynamic> a) {
    if (a['kind'] == 'service') {
      showDialog(
        context: context,
        builder: (c) => AlertDialog(
          title: Text(a['type'] ?? 'Patient request'),
          content: Text([
            '${a['patient']} (${a['patient_code']})',
            '${a['detail'] ?? ''}',
            '${a['status_detail'] ?? ''}',
          ].where((x) => x.isNotEmpty).join('\n\n')),
          actions: [
            TextButton(onPressed: () => Navigator.pop(c), child: const Text('Close')),
            FilledButton(onPressed: () { Navigator.pop(c); _accept(a['key']); }, child: const Text('Accept')),
          ],
        ),
      );
      return;
    }
    final w = a['kind'] == 'emergency'
        ? StaffEmergencyScreen(emergencyId: int.parse((a['key'] as String).split('-').last))
        : const StaffTestsTab(standalone: true);
    Navigator.push(context, MaterialPageRoute(builder: (_) => w)).then((_) => _load());
  }

  /// "Alert 2 of 3 · next 1:12" -> "Alert 3 of 3 · AI takeover in 0:40" -> "AI TAKEOVER - waiting for a human"
  String _stage(Map<String, dynamic> a) {
    final n = ((a['alert_count'] ?? 1) as int).clamp(1, 3);
    if (a['ai_takeover'] == true) return 'AI TAKEOVER - waiting for a human';
    if (a['stage'] == 'takeover_due') return 'Alert 3 of 3 · AI takeover in ${_countdown(a['takeover_at'])}';
    return 'Alert $n of 3${a['next_alert_at'] != null ? ' · next ${_countdown(a['next_alert_at'])}' : ''}';
  }

  String _countdown(String? iso) {
    final t = DateTime.tryParse(iso ?? '');
    if (t == null) return '';
    final s = t.difference(DateTime.now()).inSeconds.clamp(0, 9999);
    return '${s ~/ 60}:${(s % 60).toString().padLeft(2, '0')}';
  }

  @override
  Widget build(BuildContext context) {
    final n = _alerts.length;
    return Column(children: [
      Container(
        width: double.infinity,
        color: n > 0 ? AppColors.emergency : AppColors.ok,
        padding: const EdgeInsets.fromLTRB(16, 14, 8, 14),
        child: Row(children: [
          Icon(n > 0 ? Icons.campaign : Icons.verified, color: Colors.white, size: 30),
          const SizedBox(width: 10),
          Expanded(
            child: Text(n > 0 ? '$n unhandled request${n > 1 ? 's' : ''}' : 'No unhandled requests',
                style: const TextStyle(color: Colors.white, fontSize: 20, fontWeight: FontWeight.w800)),
          ),
          IconButton(
            tooltip: _sirenSvc.muted ? 'Unmute siren' : 'Mute siren (alerts stay visible)',
            icon: Icon(_sirenSvc.muted ? Icons.volume_off : Icons.volume_up, color: Colors.white),
            onPressed: () async {
              await _sirenSvc.setMuted(!_sirenSvc.muted);
              setState(() {});
            },
          ),
        ]),
      ),
      if (_siren.isEmpty && _loaded)
        MaterialBanner(
          backgroundColor: const Color(0xFFFEE4E2),
          content: Text('Emergency siren unavailable: ${_sirenProblem.isEmpty ? 'sound file not installed on the server' : _sirenProblem} '
              'Visual alerts still work.'),
          leading: const Icon(Icons.volume_off, color: AppColors.emergency),
          actions: const [SizedBox.shrink()],
        ),
      if (_error != null && _alerts.isNotEmpty)
        MaterialBanner(content: Text('Connection problem - showing the last list. $_error'),
            leading: const Icon(Icons.cloud_off), actions: const [SizedBox.shrink()]),
      Expanded(
        child: !_loaded
            ? (_error != null ? ErrorRetry(_error!, _load) : const Center(child: CircularProgressIndicator()))
            : RefreshIndicator(
                onRefresh: _load,
                child: ListView(padding: const EdgeInsets.all(12), children: [
                  if (_alerts.isEmpty) const EmptyState(Icons.check_circle_outline, 'All patient requests are handled'),
                  for (final a in _alerts.reversed) _card(a, a == _alerts.last),
                  if (_events.isNotEmpty) ...[
                    const SectionTitle('Monitoring (AI)'),
                    for (final e in _events) _eventCard(e),
                  ],
                ]),
              ),
      ),
    ]);
  }

  Widget _card(Map<String, dynamic> a, bool newest) {
    final escalated = a['escalated'] == true, severe = a['severe'] == true;
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Card(
        color: newest ? const Color(0xFFFEF3F2) : Colors.white,
        shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(20), side: BorderSide(color: AppColors.emergency, width: newest ? 2 : 1)),
        child: Padding(
          padding: const EdgeInsets.all(14),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Wrap(spacing: 6, runSpacing: 6, crossAxisAlignment: WrapCrossAlignment.center, children: [
              const StatusChip('pending', 'PENDING'),
              PriorityBadge(a['priority'] ?? 'medium'),
              if (newest) _tag('NEW', AppColors.emergency),
              if (severe) _tag('SEVERE', AppColors.emergency),
              if (escalated) _tag(a['ai_takeover'] == true ? 'AI TAKEOVER' : 'AI ESCALATED', Colors.black87),
            ]),
            const SizedBox(height: 8),
            Text(a['type'] ?? '', style: const TextStyle(fontSize: 17, fontWeight: FontWeight.w800)),
            Text('${a['patient']} · ${a['patient_code']} · ${a['reference']}', style: const TextStyle(color: Colors.black54)),
            const SizedBox(height: 4),
            if ((a['detail'] ?? '').toString().isNotEmpty)
              Text(a['detail'], style: const TextStyle(fontSize: 13)),
            Text('Sent ${ago(DateTime.parse(a['submitted_at']).toLocal())} · ${_stage(a)}',
                style: TextStyle(fontSize: 12, color: a['ai_takeover'] == true ? Colors.black : Colors.black54,
                    fontWeight: a['ai_takeover'] == true ? FontWeight.w700 : null)),
            const SizedBox(height: 10),
            Row(children: [
              Expanded(
                child: FilledButton.icon(
                  style: FilledButton.styleFrom(backgroundColor: AppColors.emergency, minimumSize: const Size.fromHeight(48)),
                  onPressed: () => _accept(a['key']),
                  icon: const Icon(Icons.check_circle),
                  label: const Text('Accept'),
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: OutlinedButton.icon(
                  style: OutlinedButton.styleFrom(minimumSize: const Size.fromHeight(48)),
                  onPressed: () => _open(a),
                  icon: const Icon(Icons.visibility),
                  label: const Text('Details'),
                ),
              ),
            ]),
          ]),
        ),
      ),
    );
  }

  Widget _eventCard(Map<String, dynamic> e) => Padding(
        padding: const EdgeInsets.only(bottom: 10),
        child: Card(
          child: Padding(
            padding: const EdgeInsets.all(14),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Wrap(spacing: 6, runSpacing: 6, children: [
                _tag((e['severity'] ?? '').toString().toUpperCase(),
                    e['severity'] == 'severe' ? AppColors.emergency : e['severity'] == 'urgent' ? AppColors.warn : AppColors.primary),
                _tag(e['status_display'] ?? '', Colors.black54),
                if (e['simulated'] == true) _tag('SIMULATED', AppColors.simulated),
              ]),
              const SizedBox(height: 6),
              Text('${e['patient']} · ${e['kind_display']}', style: const TextStyle(fontWeight: FontWeight.w800, fontSize: 16)),
              Text(ago(DateTime.parse(e['created_at']).toLocal()), style: const TextStyle(fontSize: 12, color: Colors.black54)),
              const SizedBox(height: 6),
              Text((e['summary'] ?? '').toString().isNotEmpty ? e['summary'] : e['reason'], style: const TextStyle(fontSize: 13)),
              const SizedBox(height: 8),
              Row(children: [
                if (e['emergency_id'] != null)
                  TextButton.icon(
                    onPressed: () => Navigator.push(context,
                        MaterialPageRoute(builder: (_) => StaffEmergencyScreen(emergencyId: e['emergency_id']))).then((_) => _load()),
                    icon: const Icon(Icons.emergency),
                    label: Text('Emergency ${e['emergency_ref']}'),
                  ),
                const Spacer(),
                OutlinedButton(
                  onPressed: () async {
                    try {
                      await ApiService.instance.post('/staff/events/${e['id']}/close/', {'note': 'handled from mobile'});
                      _load();
                    } catch (err) {
                      if (mounted) toast(context, err, error: true);
                    }
                  },
                  child: const Text('I handled it'),
                ),
              ]),
            ]),
          ),
        ),
      );

  Widget _tag(String t, Color c) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
        decoration: BoxDecoration(color: c, borderRadius: BorderRadius.circular(20)),
        child: Text(t, style: const TextStyle(color: Colors.white, fontSize: 11, fontWeight: FontWeight.w800)),
      );
}
