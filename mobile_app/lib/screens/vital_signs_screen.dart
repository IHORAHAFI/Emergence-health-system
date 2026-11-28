import 'dart:async';

import 'package:flutter/material.dart';

import '../services/api_service.dart';
import '../theme.dart';
import '../widgets/series_chart.dart';
import 'heart_rate_screen.dart';

/// Vital Signs Monitoring: three cards, all values read from Django (/api/vitals/mine/).
///   Heart rate        - CAMERA-ESTIMATED (phone camera PPG); "Heart Rate Test" opens the camera at once
///   Body temperature  - SIMULATED demonstration data
///   Blood pressure    - SIMULATED demonstration data
/// Refreshes every 10 s while open (the simulator keeps producing readings) and right after a heart-rate test.
class VitalSignsScreen extends StatefulWidget {
  const VitalSignsScreen({super.key});
  @override
  State<VitalSignsScreen> createState() => _VitalSignsScreenState();
}

class _VitalSignsScreenState extends State<VitalSignsScreen> {
  Map<String, dynamic>? _d;
  String? _error;
  Timer? _poll;

  @override
  void initState() {
    super.initState();
    _load();
    _poll = Timer.periodic(const Duration(seconds: 10), (_) => _load());
  }

  @override
  void dispose() {
    _poll?.cancel();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final d = await ApiService.instance.myVitals();
      if (mounted) {
        setState(() {
          _d = d;
          _error = null;
        });
      }
    } catch (e) {
      if (mounted) setState(() => _error = 'Could not load your vital signs: $e');
    }
  }

  Future<void> _heartRateTest() async {
    await Navigator.push(context, MaterialPageRoute(builder: (_) => const HeartRateScreen()));
    _load(); // show the new result once Django has stored it
  }

  static DateTime _t(Map m) => DateTime.parse(m['measured_at']).toLocal();
  static String _when(Map m) {
    final t = _t(m);
    String two(int v) => v.toString().padLeft(2, '0');
    return '${two(t.day)}/${two(t.month)} ${two(t.hour)}:${two(t.minute)}:${two(t.second)}';
  }

  List<Map> _hist(String k) => [for (final x in (_d?[k]?['history'] as List? ?? const [])) Map.from(x)];

  @override
  Widget build(BuildContext context) {
    final d = _d;
    return Scaffold(
      appBar: AppBar(title: const Text('Vital signs')),
      body: RefreshIndicator(
        onRefresh: _load,
        child: ListView(padding: const EdgeInsets.all(16), children: [
          if (_error != null) Text(_error!, style: const TextStyle(color: AppColors.emergency)),
          if (d == null && _error == null) const Center(child: Padding(padding: EdgeInsets.all(32), child: CircularProgressIndicator())),
          if (d != null) ...[
            _heartCard(d['heart_rate'] as Map),
            _tempCard(d['temperature'] as Map),
            _bpCard(d['blood_pressure'] as Map),
            const SizedBox(height: 8),
            Text(
                'Heart rate is estimated with your phone camera and is not a medical-grade measurement. Temperature '
                'and blood pressure are SIMULATED demonstration values, not real measurements. Do not use them to '
                'make medical decisions.',
                style: TextStyle(fontSize: 12, color: Colors.grey.shade700)),
          ],
        ]),
      ),
    );
  }

  Widget _badge(String text, Color c) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
        decoration: BoxDecoration(color: c.withValues(alpha: 0.12), borderRadius: BorderRadius.circular(20)),
        child: Text(text, style: TextStyle(color: c, fontSize: 11, fontWeight: FontWeight.w700)),
      );

  Widget _card({required IconData icon, required String title, required Widget badge, required String value,
          required List<Widget> lines, Widget? chart, Widget? action}) =>
      Card(
        margin: const EdgeInsets.only(bottom: 12),
        child: Padding(
          padding: const EdgeInsets.all(14),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Row(children: [
              Icon(icon, color: AppColors.primary),
              const SizedBox(width: 8),
              Expanded(child: Text(title, style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700))),
              badge,
            ]),
            const SizedBox(height: 8),
            Text(value, style: const TextStyle(fontSize: 30, fontWeight: FontWeight.w800)),
            ...lines,
            if (chart != null) ...[const SizedBox(height: 10), chart],
            if (action != null) ...[const SizedBox(height: 10), action],
          ]),
        ),
      );

  Widget _line(String s, {Color? color}) =>
      Padding(padding: const EdgeInsets.only(top: 2), child: Text(s, style: TextStyle(fontSize: 13, color: color ?? Colors.grey.shade700)));

  Widget _heartCard(Map hr) {
    final latest = hr['latest'] as Map?, attempt = hr['last_attempt'] as Map?;
    final failedLast = attempt != null && attempt['valid'] != true &&
        (latest == null || _t(attempt).isAfter(_t(latest)));
    final q = latest?['signal_quality'] as num?;
    final pts = [for (final m in _hist('heart_rate').reversed) (_t(m), (m['value'] as num).toDouble())];
    return _card(
      icon: Icons.favorite,
      title: 'Heart rate',
      badge: _badge('CAMERA-ESTIMATED', AppColors.primary),
      value: latest == null ? '—' : '${(latest['value'] as num).round()} BPM',
      lines: [
        if (latest != null) _line('Last measured ${_when(latest)} · ${latest['status']}'
            '${q != null ? ' · signal quality ${(q * 100).round()}%' : ''}'),
        if (latest == null) _line('No successful heart-rate test yet.'),
        if (failedLast) _line('Last test ${attempt['status']}: ${attempt['failure_reason']}', color: AppColors.warn),
        _line('Source: Smartphone Camera PPG'),
      ],
      chart: SeriesChart(unit: 'BPM', series: [ChartSeries('Camera-estimated BPM', AppColors.primary, pts)]),
      action: FilledButton.icon(
        style: FilledButton.styleFrom(minimumSize: const Size.fromHeight(52)),
        onPressed: _heartRateTest,
        icon: const Icon(Icons.camera_alt),
        label: const Text('Heart Rate Test', style: TextStyle(fontSize: 17)),
      ),
    );
  }

  Widget _tempCard(Map t) {
    final latest = t['latest'] as Map?;
    final pts = [for (final m in _hist('temperature').reversed) (_t(m), (m['value'] as num).toDouble())];
    return _card(
      icon: Icons.thermostat,
      title: 'Body temperature',
      badge: _badge('SIMULATED', AppColors.simulated),
      value: latest == null ? '—' : '${(latest['value'] as num).toStringAsFixed(1)} °C',
      lines: [
        if (latest != null) _line('${_when(latest)} · device ${latest['device'] ?? '-'}'),
        if (latest == null) _line('No simulated readings yet (the operator starts the simulator).'),
        _line('Source: SIMULATED - not a real measurement', color: AppColors.simulated),
      ],
      chart: SeriesChart(unit: '°C', series: [ChartSeries('Simulated °C', AppColors.simulated, pts, dashed: true)]),
    );
  }

  Widget _bpCard(Map b) {
    final latest = b['latest'] as Map?;
    final h = _hist('blood_pressure').reversed.toList();
    return _card(
      icon: Icons.bloodtype,
      title: 'Blood pressure',
      badge: _badge('SIMULATED', AppColors.simulated),
      value: latest == null ? '—' : '${latest['systolic']}/${latest['diastolic']} mmHg',
      lines: [
        if (latest != null) _line('Systolic ${latest['systolic']} · Diastolic ${latest['diastolic']} mmHg'),
        if (latest != null) _line('${_when(latest)} · device ${latest['device'] ?? '-'}'),
        if (latest == null) _line('No simulated readings yet (the operator starts the simulator).'),
        _line('Source: SIMULATED - not a real measurement', color: AppColors.simulated),
      ],
      chart: SeriesChart(unit: 'mmHg', series: [
        ChartSeries('Systolic', AppColors.emergency, [for (final m in h) (_t(m), (m['systolic'] as num).toDouble())], dashed: true),
        ChartSeries('Diastolic', AppColors.simulated, [for (final m in h) (_t(m), (m['diastolic'] as num).toDouble())], dashed: true),
      ]),
    );
  }
}
