import 'package:flutter/material.dart';

import '../models/test_request.dart';
import '../models/vital_reading.dart';
import '../services/api_service.dart';
import '../widgets/common.dart';
import '../widgets/vitals_chart.dart';
import 'test_detail_screen.dart';

/// Test history with results, plus a log of all measurements (real and simulated, clearly labelled).
class ResultsTab extends StatefulWidget {
  const ResultsTab({super.key});
  @override
  State<ResultsTab> createState() => ResultsTabState();
}

class ResultsTabState extends State<ResultsTab> {
  late Future<(List<TestRequest>, List<VitalReading>, Map<String, dynamic>)> _data;

  @override
  void initState() {
    super.initState();
    reload();
  }

  void reload() => setState(() {
        _data = () async {
          final api = ApiService.instance;
          return (await api.myTests(), await api.latestVitals(limit: 100), await api.myIotReadings());
        }();
      });

  @override
  Widget build(BuildContext context) {
    return DefaultTabController(
      length: 3,
      child: Column(children: [
        const Padding(
          padding: EdgeInsets.fromLTRB(16, 12, 16, 0),
          child: Align(
              alignment: Alignment.centerLeft,
              child: Text('Results', style: TextStyle(fontSize: 26, fontWeight: FontWeight.w800))),
        ),
        const TabBar(isScrollable: true, tabs: [Tab(text: 'Tests'), Tab(text: 'All measurements'), Tab(text: 'IoT sensors')]),
        Expanded(
          child: FutureBuilder(
            future: _data,
            builder: (c, s) {
              if (s.hasError) return ErrorRetry(s.error!, reload);
              if (!s.hasData) return const Center(child: CircularProgressIndicator());
              final (tests, readings, iot) = s.data!;
              return TabBarView(children: [_tests(tests), _readings(readings), _iot(iot)]);
            },
          ),
        ),
      ]),
    );
  }

  Widget _tests(List<TestRequest> tests) => RefreshIndicator(
        onRefresh: () async => reload(),
        child: tests.isEmpty
            ? ListView(children: const [EmptyState(Icons.biotech_outlined, 'No test requests yet')])
            : ListView.separated(
                padding: const EdgeInsets.all(16),
                itemCount: tests.length,
                separatorBuilder: (_, __) => const SizedBox(height: 10),
                itemBuilder: (c, i) {
                  final r = tests[i];
                  return Card(
                    child: ListTile(
                      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
                      title: Text(r.testNames, style: const TextStyle(fontWeight: FontWeight.w700)),
                      subtitle: Text('${r.reference} · ${ago(r.requestedAt)}'),
                      trailing: StatusChip(r.status, r.statusDisplay),
                      onTap: () => Navigator.push(
                              context, MaterialPageRoute(builder: (_) => TestDetailScreen(reference: r.reference)))
                          .then((_) => reload()),
                    ),
                  );
                },
              ),
      );

  Widget _readings(List<VitalReading> rs) => RefreshIndicator(
        onRefresh: () async => reload(),
        child: rs.isEmpty
            ? ListView(children: const [EmptyState(Icons.monitor_heart_outlined, 'No measurements yet')])
            : ListView.separated(
                padding: const EdgeInsets.all(16),
                itemCount: rs.length + 1,
                separatorBuilder: (_, __) => const SizedBox(height: 10),
                itemBuilder: (c, i) {
                  if (i == 0) return VitalsChart(readings: rs);  // trends over time
                  final r = rs[i - 1];
                  final parts = [
                    if (r.temperature != null) '${r.temperature!.toStringAsFixed(1)} °C',
                    if (r.heartRate != null) '${r.heartRate} BPM',
                    if (r.spo2 != null) 'SpO₂ ${r.spo2}%',
                    if (r.systolic != null) '${r.systolic}/${r.diastolic} mmHg',
                    if (r.respiratoryRate != null) '${r.respiratoryRate} /min',
                  ];
                  return Card(
                    child: Padding(
                      padding: const EdgeInsets.all(14),
                      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                        Row(children: [
                          SourceBadge(simulated: r.simulated, source: r.source),
                          const Spacer(),
                          Text(ago(r.measuredAt), style: const TextStyle(color: Colors.black54, fontSize: 12)),
                        ]),
                        const SizedBox(height: 8),
                        Text(parts.join('  ·  '), style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600)),
                        const SizedBox(height: 4),
                        Text([r.device ?? 'Manual entry', r.testRequest ?? r.emergency ?? ''].where((x) => x.isNotEmpty).join(' · '),
                            style: const TextStyle(color: Colors.black45, fontSize: 12)),
                      ]),
                    ),
                  );
                },
              ),
      );

  /// Arduino sensor readings from devices an administrator assigned to this patient. SIMULATED is always shown.
  Widget _iot(Map<String, dynamic> d) {
    final rs = List<Map<String, dynamic>>.from(d['readings'] ?? const []);
    final devices = List<Map<String, dynamic>>.from(d['devices'] ?? const []);
    final staleS = (d['stale_after_s'] as num?)?.toInt() ?? 30;
    final now = DateTime.tryParse(d['server_time'] ?? '') ?? DateTime.now();
    const units = {'C': '°C', 'bpm': 'BPM', '%': '%'};
    String value(Map<String, dynamic> r) {
      final v = r['value'] as num?;
      if (v == null) return 'No value';
      return '${r['sensor_type'] == 'temperature' ? v.toStringAsFixed(1) : v.round()} ${units[r['unit']] ?? r['unit']}';
    }

    return RefreshIndicator(
      onRefresh: () async => reload(),
      child: ListView(padding: const EdgeInsets.all(16), children: [
        if (devices.isEmpty)
          const EmptyState(Icons.memory_outlined, 'No IoT device is assigned to you')
        else
          ...devices.map((dv) => Card(
                child: ListTile(
                  leading: Icon(dv['online'] == true ? Icons.usb : Icons.usb_off,
                      color: dv['online'] == true ? Colors.green : Colors.redAccent),
                  title: Text('${dv['name']}', style: const TextStyle(fontWeight: FontWeight.w700)),
                  subtitle: Text(dv['online'] == true ? 'Connected' : 'Disconnected — values below are not current'),
                  trailing: SourceBadge(simulated: dv['mode'] == 'SIMULATED', source: 'iot'),
                ),
              )),
        const SizedBox(height: 8),
        if (devices.isNotEmpty && rs.isEmpty) const EmptyState(Icons.sensors_outlined, 'No sensor readings yet'),
        ...rs.map((r) {
          final at = DateTime.parse(r['measured_at']);
          final stale = now.difference(at).inSeconds > staleS;
          return Card(
            child: Padding(
              padding: const EdgeInsets.all(14),
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Row(children: [
                  SourceBadge(simulated: r['simulated'] == true, source: 'iot'),
                  const SizedBox(width: 8),
                  Text('${r['status']}', style: TextStyle(fontSize: 11, fontWeight: FontWeight.w700,
                      color: r['status'] == 'VALID' ? Colors.green : Colors.redAccent)),
                  const Spacer(),
                  Text('${ago(at)}${stale ? ' · old' : ''}', style: const TextStyle(color: Colors.black54, fontSize: 12)),
                ]),
                const SizedBox(height: 8),
                Text('${r['sensor_display']}: ${value(r)}',
                    style: TextStyle(fontSize: 16, fontWeight: FontWeight.w600, color: stale ? Colors.black45 : null)),
                const SizedBox(height: 4),
                Text('${r['device']} · USB serial', style: const TextStyle(color: Colors.black45, fontSize: 12)),
              ]),
            ),
          );
        }),
      ]),
    );
  }
}
