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
  String _siren = '';
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
      if (!mounted) return;
      setState(() {
        _alerts = list;
        _siren = (d['siren_url'] ?? '').toString();
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
    final w = a['kind'] == 'emergency'
        ? StaffEmergencyScreen(emergencyId: int.parse((a['key'] as String).split('-').last))
        : const StaffTestsTab(standalone: true);
    Navigator.push(context, MaterialPageRoute(builder: (_) => w)).then((_) => _load());
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
        const MaterialBanner(
          content: Text('Siren sound file is not installed on the server. Visual alerts still work.'),
          leading: Icon(Icons.volume_off, color: AppColors.warn),
          actions: [SizedBox.shrink()],
        ),
      if (_error != null && _alerts.isNotEmpty)
        MaterialBanner(content: Text('Connection problem - showing the last list. $_error'),
            leading: const Icon(Icons.cloud_off), actions: const [SizedBox.shrink()]),
      Expanded(
        child: !_loaded
            ? (_error != null ? ErrorRetry(_error!, _load) : const Center(child: CircularProgressIndicator()))
            : RefreshIndicator(
                onRefresh: _load,
                child: _alerts.isEmpty
                    ? ListView(children: const [EmptyState(Icons.check_circle_outline, 'All patient requests are handled')])
                    : ListView(padding: const EdgeInsets.all(12), children: [
                        for (final a in _alerts.reversed) _card(a, a == _alerts.last),
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
              if (escalated) _tag('AI ESCALATED', Colors.black87),
            ]),
            const SizedBox(height: 8),
            Text(a['type'] ?? '', style: const TextStyle(fontSize: 17, fontWeight: FontWeight.w800)),
            Text('${a['patient']} · ${a['patient_code']} · ${a['reference']}', style: const TextStyle(color: Colors.black54)),
            const SizedBox(height: 4),
            Text(
                'Sent ${ago(DateTime.parse(a['submitted_at']).toLocal())} · alert ${a['alert_count'] ?? 1}'
                '${a['kind'] == 'emergency' && !escalated ? ' of 3' : ''}'
                '${a['next_alert_at'] != null ? ' · next ${_countdown(a['next_alert_at'])}' : ''}',
                style: const TextStyle(fontSize: 12, color: Colors.black54)),
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

  Widget _tag(String t, Color c) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
        decoration: BoxDecoration(color: c, borderRadius: BorderRadius.circular(20)),
        child: Text(t, style: const TextStyle(color: Colors.white, fontSize: 11, fontWeight: FontWeight.w800)),
      );
}
