import 'dart:async';

import 'package:flutter/material.dart';

import '../models/test_request.dart';
import '../services/api_service.dart';
import '../services/ble_service.dart';
import '../theme.dart';
import '../widgets/common.dart';
import 'ble_connect_screen.dart';

/// Live status + results of one test request. Polls the backend while the request is open,
/// so simulated readings started by the operator and real Bluetooth readings both appear automatically.
class TestDetailScreen extends StatefulWidget {
  final String reference;
  const TestDetailScreen({super.key, required this.reference});
  @override
  State<TestDetailScreen> createState() => _TestDetailScreenState();
}

class _TestDetailScreenState extends State<TestDetailScreen> {
  TestRequest? _req;
  Object? _error;
  Timer? _timer;
  StreamSubscription<BleMeasurement>? _bleSub;
  String? _deviceName;
  String? _lastSent;

  @override
  void initState() {
    super.initState();
    _load();
    _timer = Timer.periodic(const Duration(seconds: 4), (_) => _load());
  }

  @override
  void dispose() {
    _timer?.cancel();
    _bleSub?.cancel();
    BleService.instance.disconnect();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final r = await ApiService.instance.getTest(widget.reference);
      if (!mounted) return;
      setState(() {
        _req = r;
        _error = null;
      });
      if (!r.isOpen) {
        _timer?.cancel();
        _stopDevice();
      }
    } catch (e) {
      if (mounted) setState(() => _error = e);
    }
  }

  Future<void> _connectDevice() async {
    final p = await Navigator.push<HealthProfile>(context, MaterialPageRoute(builder: (_) => const BleConnectScreen()));
    if (p == null || !mounted) return;
    setState(() => _deviceName = BleService.instance.device?.platformName.isNotEmpty == true
        ? BleService.instance.device!.platformName
        : p.label);
    await _bleSub?.cancel();
    _bleSub = BleService.instance.measurements.listen((m) async {
      try {
        await ApiService.instance.sendTestReading(widget.reference, m.values, m.device);
        if (mounted) setState(() => _lastSent = 'Sent ${m.values.values.join(' / ')} · ${ago(m.at)}');
        _load();
      } catch (e) {
        if (mounted) toast(context, e, error: true);
      }
    });
  }

  Future<void> _stopDevice() async {
    await _bleSub?.cancel();
    _bleSub = null;
    await BleService.instance.disconnect();
    if (mounted) setState(() => _deviceName = null);
  }

  Future<void> _cancel() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Cancel this request?'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('No')),
          FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('Yes, cancel')),
        ],
      ),
    );
    if (ok != true) return;
    try {
      await ApiService.instance.cancelTest(widget.reference);
      _load();
    } catch (e) {
      if (mounted) toast(context, e, error: true);
    }
  }

  @override
  Widget build(BuildContext context) {
    final r = _req;
    return Scaffold(
      appBar: AppBar(title: Text(widget.reference, style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w700))),
      body: r == null
          ? (_error != null ? ErrorRetry(_error!, _load) : const Center(child: CircularProgressIndicator()))
          : RefreshIndicator(
              onRefresh: _load,
              child: ListView(padding: const EdgeInsets.all(16), children: [
                Text(r.testNames, style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w800)),
                const SizedBox(height: 12),
                _steps(r.status),
                const SizedBox(height: 8),
                Text(r.statusText, style: const TextStyle(color: Colors.black54)),
                if (r.hasSimulated) ...[const SizedBox(height: 12), _simulatedBanner()],
                const SectionTitle('Measurements'),
                GridView.count(
                  crossAxisCount: 2,
                  shrinkWrap: true,
                  physics: const NeverScrollableScrollPhysics(),
                  mainAxisSpacing: 12,
                  crossAxisSpacing: 12,
                  childAspectRatio: 1.15,
                  children: [
                    for (final code in r.tests) _card(code, r),
                  ],
                ),
                if (r.isTesting) ...[const SizedBox(height: 16), _devicePanel()],
                const SizedBox(height: 16),
                if (r.status == 'pending' || r.status == 'accepted')
                  OutlinedButton.icon(
                    onPressed: _cancel,
                    icon: const Icon(Icons.close),
                    label: const Text('Cancel request'),
                    style: OutlinedButton.styleFrom(foregroundColor: AppColors.emergency),
                  ),
              ]),
            ),
    );
  }

  Widget _card(String code, TestRequest r) {
    final key = summaryKeyToCode.entries.firstWhere((e) => e.value == code).key;
    final res = r.summary[key];
    final fields = key == 'blood_pressure' ? const ['systolic_mmhg', 'diastolic_mmhg'] : [key];
    final flagged = res != null && fields.any(res.flags.containsKey);
    return VitalCard(
      code: code,
      value: res?.value?.toString(),
      simulated: res?.simulated ?? false,
      source: res?.source ?? 'bluetooth',
      caption: res?.at != null ? ago(res!.at!) : (r.isTesting ? 'Waiting…' : null),
      alert: flagged,
    );
  }

  Widget _steps(String status) {
    const order = ['pending', 'accepted', 'testing', 'completed'];
    const labels = ['Sent', 'Accepted', 'Testing', 'Results'];
    if (status == 'cancelled') return const StatusChip('cancelled', 'Cancelled');
    final idx = order.indexOf(status);
    return Row(children: [
      for (var i = 0; i < order.length; i++) ...[
        Column(children: [
          CircleAvatar(
            radius: 14,
            backgroundColor: i <= idx ? AppColors.primary : const Color(0xFFE4E7EC),
            child: i < idx || status == 'completed'
                ? const Icon(Icons.check, size: 16, color: Colors.white)
                : Text('${i + 1}', style: TextStyle(fontSize: 12, color: i <= idx ? Colors.white : Colors.black45)),
          ),
          const SizedBox(height: 4),
          Text(labels[i], style: TextStyle(fontSize: 11, fontWeight: i == idx ? FontWeight.w700 : null)),
        ]),
        if (i < order.length - 1)
          Expanded(
              child: Container(
                  height: 2,
                  margin: const EdgeInsets.only(bottom: 18),
                  color: i < idx ? AppColors.primary : const Color(0xFFE4E7EC))),
      ],
    ]);
  }

  Widget _simulatedBanner() => Container(
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(
            color: AppColors.simulated.withValues(alpha: .08), borderRadius: BorderRadius.circular(14)),
        child: const Row(children: [
          Icon(Icons.science_outlined, color: AppColors.simulated),
          SizedBox(width: 10),
          Expanded(
              child: Text('Includes SIMULATED values from a virtual device. They are not real measurements.',
                  style: TextStyle(color: AppColors.simulated, fontWeight: FontWeight.w600))),
        ]),
      );

  Widget _devicePanel() => Card(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: _deviceName == null
              ? Column(children: [
                  const Text('Have your own Bluetooth device?', style: TextStyle(fontWeight: FontWeight.w600)),
                  const SizedBox(height: 10),
                  FilledButton.icon(
                      onPressed: _connectDevice, icon: const Icon(Icons.bluetooth), label: const Text('Connect device')),
                ])
              : Row(children: [
                  const Icon(Icons.bluetooth_connected, color: AppColors.ok),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      Text(_deviceName!, style: const TextStyle(fontWeight: FontWeight.w700)),
                      Text(_lastSent ?? 'Take a measurement on the device',
                          style: const TextStyle(color: Colors.black54, fontSize: 12)),
                    ]),
                  ),
                  TextButton(onPressed: _stopDevice, child: const Text('Disconnect')),
                ]),
        ),
      );
}
