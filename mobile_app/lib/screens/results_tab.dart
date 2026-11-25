import 'package:flutter/material.dart';

import '../models/test_request.dart';
import '../models/vital_reading.dart';
import '../services/api_service.dart';
import '../widgets/common.dart';
import 'test_detail_screen.dart';

/// Test history with results, plus a log of all measurements (real and simulated, clearly labelled).
class ResultsTab extends StatefulWidget {
  const ResultsTab({super.key});
  @override
  State<ResultsTab> createState() => ResultsTabState();
}

class ResultsTabState extends State<ResultsTab> {
  late Future<(List<TestRequest>, List<VitalReading>)> _data;

  @override
  void initState() {
    super.initState();
    reload();
  }

  void reload() => setState(() {
        _data = () async {
          final api = ApiService.instance;
          return (await api.myTests(), await api.latestVitals(limit: 50));
        }();
      });

  @override
  Widget build(BuildContext context) {
    return DefaultTabController(
      length: 2,
      child: Column(children: [
        const Padding(
          padding: EdgeInsets.fromLTRB(16, 12, 16, 0),
          child: Align(
              alignment: Alignment.centerLeft,
              child: Text('Results', style: TextStyle(fontSize: 26, fontWeight: FontWeight.w800))),
        ),
        const TabBar(tabs: [Tab(text: 'Tests'), Tab(text: 'All measurements')]),
        Expanded(
          child: FutureBuilder(
            future: _data,
            builder: (c, s) {
              if (s.hasError) return ErrorRetry(s.error!, reload);
              if (!s.hasData) return const Center(child: CircularProgressIndicator());
              final (tests, readings) = s.data!;
              return TabBarView(children: [_tests(tests), _readings(readings)]);
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
                itemCount: rs.length,
                separatorBuilder: (_, __) => const SizedBox(height: 10),
                itemBuilder: (c, i) {
                  final r = rs[i];
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
}
