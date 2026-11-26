import 'dart:async';

import 'package:flutter/material.dart';
import 'package:latlong2/latlong.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../models/emergency.dart';
import '../../models/vital_reading.dart';
import '../../services/api_service.dart';
import '../../theme.dart';
import '../../widgets/common.dart';
import '../../widgets/emergency_map.dart';
import '../../widgets/priority_badge.dart';
import '../../widgets/vitals_chart.dart';

/// One emergency for operators and medical staff. Everything shown comes from role-checked endpoints:
/// the backend returns 404 if this user may not see the patient.
class StaffEmergencyScreen extends StatefulWidget {
  final int emergencyId;
  const StaffEmergencyScreen({super.key, required this.emergencyId});
  @override
  State<StaffEmergencyScreen> createState() => _StaffEmergencyScreenState();
}

// Mirrors emergency.services.ALLOWED_TRANSITIONS (the server validates every change anyway).
const _next = {
  'pending': ['cancelled'],
  'assigned': ['en_route', 'cancelled'],
  'en_route': ['on_scene', 'cancelled'],
  'on_scene': ['transporting', 'completed', 'cancelled'],
  'transporting': ['arrived'],
  'arrived': ['completed'],
};
const _statusNames = {
  'en_route': 'Ambulance en route', 'on_scene': 'On scene', 'transporting': 'Transporting',
  'arrived': 'Arrived at hospital', 'completed': 'Completed', 'cancelled': 'Cancel request',
};

class _StaffEmergencyScreenState extends State<StaffEmergencyScreen> {
  Emergency? _em;
  List<VitalReading> _vitals = [];
  String? _vitalsNote;
  Object? _error;
  String _role = '';
  Timer? _timer;
  final _msg = TextEditingController();

  bool get _operator => _role == 'operator' || _role == 'admin';

  @override
  void initState() {
    super.initState();
    ApiService.instance.me().then((m) => mounted ? setState(() => _role = m['role'] ?? '') : null).catchError((_) {});
    _load();
    _timer = Timer.periodic(const Duration(seconds: 10), (_) => _load());
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final e = await ApiService.instance.getEmergency(widget.emergencyId);
      List<VitalReading> v = [];
      String? note;
      try {
        v = await ApiService.instance.emergencyVitals(e.reference);
      } catch (err) {
        note = '$err';
      }
      if (!mounted) return;
      setState(() {
        _em = e;
        _vitals = v;
        _vitalsNote = note;
        _error = null;
      });
    } catch (err) {
      if (mounted) setState(() => _error = err);
    }
  }

  Future<void> _do(Future<void> Function() f, String ok) async {
    try {
      await f();
      if (mounted) toast(context, ok);
      _load();
    } catch (e) {
      if (mounted) toast(context, e, error: true);
    }
  }

  @override
  Widget build(BuildContext context) {
    final e = _em;
    return Scaffold(
      appBar: AppBar(title: Text(e?.reference ?? 'Emergency', style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w700))),
      body: e == null
          ? (_error != null ? ErrorRetry(_error!, _load) : const Center(child: CircularProgressIndicator()))
          : RefreshIndicator(onRefresh: _load, child: _body(e)),
    );
  }

  Widget _body(Emergency e) {
    final r = e.raw;
    final esc = e.escalation;
    final nexts = (_next[e.status] ?? [])
        .where((s) => _operator || (_role == 'hospital' && (s == 'arrived' || s == 'completed')))
        .toList();
    return ListView(padding: const EdgeInsets.all(16), children: [
      Row(children: [
        Expanded(child: Text(e.typeDisplay, style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w800))),
        PriorityBadge(e.priority),
      ]),
      const SizedBox(height: 6),
      Wrap(spacing: 6, runSpacing: 6, children: [
        StatusChip(e.accepted ? 'accepted' : 'pending', e.operatorLabel),
        Chip(label: Text(e.statusDisplay), visualDensity: VisualDensity.compact),
        if (e.acceptedBy != null) Chip(avatar: const Icon(Icons.person, size: 16), label: Text('Accepted by ${e.acceptedBy}'), visualDensity: VisualDensity.compact),
      ]),
      if (_operator && !e.accepted && e.isActive) ...[
        const SizedBox(height: 12),
        FilledButton.icon(
          style: FilledButton.styleFrom(backgroundColor: AppColors.emergency),
          onPressed: () => _do(() => ApiService.instance.acceptEmergency(e.id), 'Accepted - the siren stops for this request'),
          icon: const Icon(Icons.check_circle),
          label: const Text('Accept request'),
        ),
      ],
      const SectionTitle('Patient'),
      Card(
        child: Padding(
          padding: const EdgeInsets.all(14),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text((r['patient_name'] ?? '').toString().isNotEmpty ? r['patient_name'] : e.patientName,
                style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 16)),
            if (r['age'] != null) Text('Age ${r['age']}${(r['gender'] ?? '') != '' ? ' · ${r['gender']}' : ''}'),
            const SizedBox(height: 6),
            Wrap(spacing: 6, runSpacing: 6, children: [
              if (r['conscious'] == false) _flag('Not conscious'),
              if (r['breathing_difficulty'] == true) _flag('Breathing difficulty'),
              if (r['severe_bleeding'] == true) _flag('Severe bleeding'),
              if (r['chest_pain'] == true) _flag('Chest pain'),
              if (r['seizure'] == true) _flag('Seizure'),
              if (r['pregnant'] == true) _flag('Pregnant'),
              if ((r['pain_level'] ?? 0) > 0) Chip(label: Text('Pain ${r['pain_level']}/10'), visualDensity: VisualDensity.compact),
            ]),
            if ((r['symptoms'] ?? '').toString().isNotEmpty) Padding(padding: const EdgeInsets.only(top: 6), child: Text('Symptoms: ${r['symptoms']}')),
            if ((r['description'] ?? '').toString().isNotEmpty) Padding(padding: const EdgeInsets.only(top: 6), child: Text(r['description'])),
            if (e.patientPhone.isNotEmpty || (r['contact_phone'] ?? '').toString().isNotEmpty) ...[
              const SizedBox(height: 8),
              OutlinedButton.icon(
                onPressed: () => launchUrl(Uri(scheme: 'tel', path: (r['contact_phone'] ?? '').toString().isNotEmpty ? r['contact_phone'] : e.patientPhone)),
                icon: const Icon(Icons.call),
                label: const Text('Call patient'),
              ),
            ],
          ]),
        ),
      ),
      if (esc.isNotEmpty) ...[
        const SectionTitle('AI escalation summary'),
        Card(
          color: const Color(0xFFF2F4F7),
          child: Padding(
            padding: const EdgeInsets.all(14),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(esc['reason'] == 'severe_symptoms'
                  ? 'Escalated immediately: ${((esc['severe_reasons'] ?? []) as List).join(', ')}'
                  : 'Escalated after ${esc['alert_count']} alerts with no operator',
                  style: const TextStyle(fontWeight: FontWeight.w700)),
              const SizedBox(height: 6),
              Text('Medical test: ${(esc['test'] ?? {})['why'] ?? 'not started'}', style: const TextStyle(fontSize: 13)),
              if ((esc['vitals_note'] ?? '').toString().isNotEmpty)
                Text(esc['vitals_note'], style: const TextStyle(fontSize: 13)),
              const SizedBox(height: 6),
              const Text('Sent to the patient:', style: TextStyle(fontSize: 12, color: Colors.black54)),
              Text(esc['patient_message'] ?? '', style: const TextStyle(fontSize: 13)),
              const SizedBox(height: 6),
              const Text('AI information only - a health professional must take responsibility.',
                  style: TextStyle(fontSize: 11, color: Colors.black45)),
            ]),
          ),
        ),
      ],
      const SectionTitle('Location'),
      SizedBox(
        height: 200,
        child: ClipRRect(
          borderRadius: BorderRadius.circular(20),
          child: EmergencyMap(
            patient: e.latitude != null ? LatLng(e.latitude!, e.longitude!) : null,
            patientLabel: e.locationLabel,
            ambulance: e.ambulanceLat != null ? LatLng(e.ambulanceLat!, e.ambulanceLng!) : null,
            hospital: e.hospital != null ? LatLng(e.hospital!.latitude, e.hospital!.longitude) : null,
          ),
        ),
      ),
      Padding(
        padding: const EdgeInsets.only(top: 6),
        child: Text([
          if (e.ambulancePlate != null) 'Ambulance ${e.ambulancePlate}${e.etaMinutes != null ? ' · ETA ~${e.etaMinutes!.toStringAsFixed(0)} min' : ''}',
          if (e.hospital != null) 'Hospital: ${e.hospital!.name}',
          '${e.locationLabel}'
              '${e.location['accuracy_m'] != null ? ' · ±${(e.location['accuracy_m'] as num).round()} m' : ''}'
              '${e.location['updated_at'] != null ? ' · updated ${ago(DateTime.parse(e.location['updated_at']).toLocal())}' : ''}'
              '${(e.location['note'] ?? '').toString().isNotEmpty ? ' · ${e.location['note']}' : ''}',
        ].join('\n'), style: const TextStyle(fontSize: 13, color: Colors.black54)),
      ),
      const SectionTitle('Vital signs'),
      if (_vitalsNote != null)
        Text('Not available: $_vitalsNote', style: const TextStyle(color: Colors.black54))
      else if (_vitals.isEmpty)
        const Text('No vital signs shared for this emergency.', style: TextStyle(color: Colors.black54))
      else ...[
        VitalsChart(readings: _vitals),
        const SizedBox(height: 8),
        for (final v in _vitals.take(5))
          Card(
            child: ListTile(
              dense: true,
              title: Text([
                if (v.heartRate != null) '${v.heartRate} BPM',
                if (v.spo2 != null) 'SpO₂ ${v.spo2}%',
                if (v.temperature != null) '${v.temperature!.toStringAsFixed(1)} °C',
                if (v.systolic != null) '${v.systolic}/${v.diastolic}',
                if (v.respiratoryRate != null) 'RR ${v.respiratoryRate}',
              ].join(' · ')),
              subtitle: Text('${ago(v.measuredAt)}${v.news2 != null ? ' · NEWS2 ${v.news2}' : ''}'),
              trailing: SourceBadge(simulated: v.simulated, source: v.source),
            ),
          ),
      ],
      if (nexts.isNotEmpty) ...[
        const SectionTitle('Update status'),
        Wrap(spacing: 8, runSpacing: 8, children: [
          for (final s in nexts)
            OutlinedButton(
              style: s == 'cancelled' ? OutlinedButton.styleFrom(foregroundColor: AppColors.emergency) : null,
              onPressed: () => _do(() => ApiService.instance.updateEmergencyStatus(e.id, s), 'Status updated'),
              child: Text(_statusNames[s] ?? s),
            ),
        ]),
      ],
      const SectionTitle('Message the patient'),
      Row(children: [
        Expanded(child: TextField(controller: _msg, maxLength: 500, decoration: const InputDecoration(hintText: 'Short message', counterText: ''))),
        const SizedBox(width: 8),
        IconButton.filled(
          onPressed: () {
            final t = _msg.text.trim();
            if (t.isEmpty) return;
            _do(() => ApiService.instance.sendEmergencyMessage(e.id, t), 'Message sent').then((_) => _msg.clear());
          },
          icon: const Icon(Icons.send),
        ),
      ]),
      const SectionTitle('History'),
      for (final l in e.logs.reversed.take(12))
        Padding(
          padding: const EdgeInsets.only(bottom: 4),
          child: Text('• ${l['status']} ${l['note'] ?? ''}', style: const TextStyle(fontSize: 13, color: Colors.black87)),
        ),
      const SizedBox(height: 24),
    ]);
  }

  Widget _flag(String t) => Chip(
        label: Text(t, style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w700)),
        backgroundColor: AppColors.emergency,
        visualDensity: VisualDensity.compact,
      );
}
