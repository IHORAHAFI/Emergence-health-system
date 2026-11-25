import 'package:flutter/material.dart';

import '../../models/vital_reading.dart';
import '../../services/api_service.dart';
import '../../services/location_service.dart';
import '../../theme.dart';
import '../../widgets/common.dart';
import '../../widgets/communication_board.dart';
import '../../widgets/vitals_chart.dart';

/// What one patient allowed this caregiver to see and do. Everything sent from here is recorded as coming
/// from the CAREGIVER (never as the patient's own words).
class CaregiverPatientScreen extends StatefulWidget {
  final int patientId;
  const CaregiverPatientScreen({super.key, required this.patientId});
  @override
  State<CaregiverPatientScreen> createState() => _CaregiverPatientScreenState();
}

class _CaregiverPatientScreenState extends State<CaregiverPatientScreen> {
  Map<String, dynamic>? _d;
  List<Map<String, dynamic>> _events = [];
  Object? _error;
  bool _busy = false;

  int get _pid => widget.patientId;
  Map<String, dynamic> get _perm => Map<String, dynamic>.from(_d?['permissions'] ?? {});

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final d = Map<String, dynamic>.from(await ApiService.instance.get('/caregivers/patients/$_pid/'));
      final ev = (await ApiService.instance.get('/monitoring/events/', {'open': '1'}) as List)
          .map((e) => Map<String, dynamic>.from(e)).where((e) => e['patient_id'] == _pid).toList();
      if (mounted) setState(() {
        _d = d;
        _events = ev;
        _error = null;
      });
    } catch (e) {
      if (mounted) setState(() => _error = e);
    }
  }

  Future<void> _run(Future<void> Function() f, String ok) async {
    setState(() => _busy = true);
    try {
      await f();
      if (mounted) toast(context, ok);
      _load();
    } catch (e) {
      if (mounted) toast(context, e, error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _emergency() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        icon: const Icon(Icons.emergency, color: AppColors.emergency, size: 40),
        title: Text('Get emergency help for ${_d?['name']}?'),
        content: const Text('Your current location will be sent as the place where help is needed.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Cancel')),
          FilledButton(style: FilledButton.styleFrom(backgroundColor: AppColors.emergency),
              onPressed: () => Navigator.pop(c, true), child: const Text('Get help')),
        ],
      ),
    );
    if (ok != true) return;
    await _run(() async {
      final pos = await LocationService.current();
      await ApiService.instance.post('/caregivers/patients/$_pid/emergency/', {
        'latitude': pos.latitude, 'longitude': pos.longitude, 'description': 'Caregiver requested help',
      });
    }, 'Emergency help requested');
  }

  Future<void> _board({Map<String, dynamic>? event}) async {
    final a = await showModalBottomSheet<BoardAnswer>(
      context: context,
      isScrollControlled: true,
      builder: (c) => SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: SingleChildScrollView(
            child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
              Text(event?['prompt'] ?? 'How is ${_d?['name']}?', style: const TextStyle(fontSize: 20, fontWeight: FontWeight.w700)),
              const SizedBox(height: 12),
              CommunicationBoard(onAnswer: (x) => Navigator.pop(c, x)),
            ]),
          ),
        ),
      ),
    );
    if (a == null) return;
    if (event != null) {
      await _run(() => ApiService.instance.post('/monitoring/events/${event['id']}/respond/', {
            'answer': a.answer, 'symptoms': a.symptoms, if (a.pain != null) 'pain': a.pain, 'channel': 'caregiver',
          }), 'Answer sent');
    } else {
      await _run(() => ApiService.instance.post('/caregivers/patients/$_pid/report/',
          {'symptoms': a.symptoms, 'text': a.pain != null ? 'pain ${a.pain}/10' : ''}), 'Report sent');
    }
  }

  Future<void> _test() async {
    final code = await showModalBottomSheet<String>(
      context: context,
      builder: (c) => SafeArea(
        child: ListView(shrinkWrap: true, children: [
          for (final e in vitals.entries)
            ListTile(leading: Icon(e.value.icon, color: e.value.color), title: Text(e.value.label),
                onTap: () => Navigator.pop(c, e.key)),
          ListTile(leading: const Icon(Icons.health_and_safety), title: const Text('All vital signs'),
              onTap: () => Navigator.pop(c, 'all')),
        ]),
      ),
    );
    if (code == null) return;
    await _run(() => ApiService.instance.post('/caregivers/patients/$_pid/tests/',
        {'tests': code == 'all' ? vitals.keys.toList() : [code]}), 'Test requested');
  }

  Widget _action(String label, IconData icon, Color color, VoidCallback? onTap) => Expanded(
        child: Semantics(
          button: true,
          label: label,
          child: Material(
            color: onTap == null ? Colors.grey.shade300 : color,
            borderRadius: BorderRadius.circular(20),
            child: InkWell(
              borderRadius: BorderRadius.circular(20),
              onTap: _busy ? null : onTap,
              child: ExcludeSemantics(child: SizedBox(
                height: 96,
                child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
                  Icon(icon, color: Colors.white, size: 34),
                  Text(label, textAlign: TextAlign.center, style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w800)),
                ]),
              )),
            ),
          ),
        ),
      );

  @override
  Widget build(BuildContext context) {
    final d = _d;
    return Scaffold(
      appBar: AppBar(title: Text(d?['name'] ?? 'Patient')),
      body: d == null
          ? (_error != null ? ErrorRetry(_error!, _load) : const Center(child: CircularProgressIndicator()))
          : RefreshIndicator(
              onRefresh: _load,
              child: ListView(padding: const EdgeInsets.all(16), children: [
                if (d['active_emergency'] != null)
                  Card(
                    color: const Color(0xFFFEF3F2),
                    child: ListTile(
                      leading: const Icon(Icons.emergency_share, color: AppColors.emergency),
                      title: Text('Emergency ${d['active_emergency']['reference']}', style: const TextStyle(fontWeight: FontWeight.w700)),
                      subtitle: Text('${d['active_emergency']['label']} · ${d['active_emergency']['status']}'),
                    ),
                  ),
                for (final e in _events)
                  Card(
                    color: const Color(0xFFFFFAEB),
                    child: ListTile(
                      leading: const Icon(Icons.smart_toy, color: AppColors.warn),
                      title: Text(e['kind_display'] ?? '', style: const TextStyle(fontWeight: FontWeight.w700)),
                      subtitle: Text('${e['status_display']}\n${e['prompt']}'),
                      isThreeLine: true,
                      trailing: _perm['can_respond_checkins'] == true
                          ? FilledButton(onPressed: () => _board(event: e), child: const Text('Answer'))
                          : null,
                    ),
                  ),
                const SizedBox(height: 8),
                Row(children: [
                  _action('Emergency help', Icons.emergency, AppColors.emergency,
                      _perm['can_request_emergency'] == true ? _emergency : null),
                  const SizedBox(width: 10),
                  _action('Report symptoms', Icons.forum, AppColors.warn, _perm['can_respond_checkins'] == true ? _board : null),
                  const SizedBox(width: 10),
                  _action('Request test', Icons.biotech, AppColors.primary, _perm['can_request_tests'] == true ? _test : null),
                ]),
                const SectionTitle('Vital signs'),
                if (d['vitals'] == null)
                  const Text('The patient has not allowed you to see their vital signs.', style: TextStyle(color: Colors.black54))
                else if ((d['vitals'] as List).isEmpty)
                  const Text('No measurements yet.', style: TextStyle(color: Colors.black54))
                else
                  VitalsChart(readings: [for (final v in d['vitals'] as List) VitalReading.fromJson(Map<String, dynamic>.from(v))]),
                const SizedBox(height: 16),
                const Text('Everything you send is recorded as coming from you, the caregiver.',
                    style: TextStyle(fontSize: 12, color: Colors.black54)),
              ]),
            ),
    );
  }
}
