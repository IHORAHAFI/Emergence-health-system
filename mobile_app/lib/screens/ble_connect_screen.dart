import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_blue_plus/flutter_blue_plus.dart';

import '../services/ble_service.dart';
import '../theme.dart';
import '../widgets/common.dart';

/// Scans for REAL nearby health devices (standard Bluetooth health profiles only) and connects one.
/// Pops with the connected [HealthProfile], or null.
class BleConnectScreen extends StatefulWidget {
  const BleConnectScreen({super.key});
  @override
  State<BleConnectScreen> createState() => _BleConnectScreenState();
}

class _BleConnectScreenState extends State<BleConnectScreen> {
  final _ble = BleService.instance;
  List<ScanResult> _results = [];
  StreamSubscription? _sub;
  String? _problem;
  String? _connecting;
  bool _scanning = false;

  @override
  void initState() {
    super.initState();
    _start();
  }

  Future<void> _start() async {
    final reason = await _ble.unavailableReason();
    if (!mounted) return;
    if (reason != null) {
      setState(() => _problem = reason);
      return;
    }
    setState(() {
      _problem = null;
      _scanning = true;
      _results = [];
    });
    await _sub?.cancel();
    try {
      _sub = _ble.scan().listen((r) => mounted ? setState(() => _results = r) : null);
      FlutterBluePlus.isScanning.where((s) => !s).first.then((_) => mounted ? setState(() => _scanning = false) : null);
    } catch (e) {
      setState(() {
        _problem = '$e';
        _scanning = false;
      });
    }
  }

  Future<void> _connect(ScanResult r) async {
    setState(() => _connecting = r.device.remoteId.str);
    try {
      final p = await _ble.connect(r.device);
      if (mounted) Navigator.pop(context, p);
    } catch (e) {
      if (mounted) toast(context, e.toString().replaceFirst('Exception: ', ''), error: true);
    } finally {
      if (mounted) setState(() => _connecting = null);
    }
  }

  @override
  void dispose() {
    _sub?.cancel();
    _ble.stopScan();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Connect device')),
      body: ListView(padding: const EdgeInsets.all(16), children: [
        Card(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Row(children: [
              const Icon(Icons.info_outline, color: AppColors.primary),
              const SizedBox(width: 12),
              Expanded(
                child: Text('Turn on your thermometer, oximeter, heart-rate or blood-pressure monitor.',
                    style: TextStyle(color: Colors.grey.shade800)),
              ),
            ]),
          ),
        ),
        const SizedBox(height: 12),
        if (_problem != null)
          EmptyState(Icons.bluetooth_disabled, _problem!,
              action: OutlinedButton.icon(onPressed: _start, icon: const Icon(Icons.refresh), label: const Text('Try again')))
        else ...[
          if (_scanning) const LinearProgressIndicator(),
          if (_results.isEmpty && !_scanning)
            EmptyState(Icons.bluetooth_searching, 'No compatible device found',
                action: OutlinedButton.icon(onPressed: _start, icon: const Icon(Icons.refresh), label: const Text('Scan again'))),
          for (final r in _results)
            Padding(
              padding: const EdgeInsets.only(top: 10),
              child: Card(
                child: ListTile(
                  leading: const CircleAvatar(child: Icon(Icons.bluetooth)),
                  title: Text(r.device.platformName.isEmpty ? 'Health device' : r.device.platformName),
                  subtitle: Text(_kind(r)),
                  trailing: _connecting == r.device.remoteId.str
                      ? const SizedBox(width: 24, height: 24, child: CircularProgressIndicator(strokeWidth: 2))
                      : FilledButton(
                          style: FilledButton.styleFrom(minimumSize: const Size(90, 40)),
                          onPressed: _connecting == null ? () => _connect(r) : null,
                          child: const Text('Connect')),
                ),
              ),
            ),
        ],
      ]),
    );
  }

  String _kind(ScanResult r) {
    final uuids = r.advertisementData.serviceUuids;
    return profiles.where((p) => uuids.contains(p.service)).map((p) => p.label).join(', ').ifEmpty('Health device');
  }
}

extension on String {
  String ifEmpty(String other) => isEmpty ? other : this;
}
