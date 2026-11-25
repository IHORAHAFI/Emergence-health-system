import 'package:flutter/material.dart';

import '../models/emergency.dart';
import '../models/vital_reading.dart';
import '../services/api_service.dart';
import '../theme.dart';
import '../widgets/common.dart';
import 'accessibility_settings_screen.dart';
import 'assistant_screen.dart';
import 'home_monitoring_screen.dart';
import 'speak_for_me_screen.dart';
import 'voice_assistant_screen.dart';
import 'diseases_screen.dart';
import 'emergency_history_screen.dart';
import 'emergency_request_screen.dart';
import 'nearby_hospitals_screen.dart';
import 'tracking_screen.dart';

class HomeTab extends StatefulWidget {
  final void Function(int tab) onGo;
  const HomeTab({super.key, required this.onGo});
  @override
  State<HomeTab> createState() => _HomeTabState();
}

class _HomeTabState extends State<HomeTab> {
  String _name = '';
  Emergency? _active;
  Map<String, LatestVital> _latest = {};
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final api = ApiService.instance;
    try {
      final r = await Future.wait([api.me(), api.myEmergencies(activeOnly: true), api.latestVitals(limit: 20)]);
      final me = r[0] as Map<String, dynamic>;
      if (!mounted) return;
      setState(() {
        _name = (me['first_name'] ?? '').toString().isNotEmpty ? me['first_name'] : me['username'];
        final active = r[1] as List<Emergency>;
        _active = active.isEmpty ? null : active.first;
        _latest = latestByVital(r[2] as List<VitalReading>);
      });
    } catch (_) {
      // Home stays usable (SOS) even when the network is down.
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  void _open(Widget w) => Navigator.push(context, MaterialPageRoute(builder: (_) => w)).then((_) => _load());

  @override
  Widget build(BuildContext context) {
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(padding: const EdgeInsets.fromLTRB(16, 12, 16, 24), children: [
        Text(_name.isEmpty ? 'Hello' : 'Hello, $_name',
            style: const TextStyle(fontSize: 26, fontWeight: FontWeight.w800)),
        const Text('How can we help today?', style: TextStyle(color: Colors.black54)),
        const SizedBox(height: 16),
        if (_active != null) _activeCard(_active!) else _sosButton(),
        const SizedBox(height: 12),
        Row(children: [
          Expanded(child: _bigAction(Icons.mic, 'Talk to assistant', AppColors.primary,
              () => _open(const VoiceAssistantScreen()))),
          const SizedBox(width: 12),
          Expanded(child: _bigAction(Icons.touch_app, "I can't speak", const Color(0xFF7F56D9),
              () => _open(const SpeakForMeScreen()))),
        ]),
        const SectionTitle('Latest vital signs'),
        if (_loading)
          const Center(child: Padding(padding: EdgeInsets.all(24), child: CircularProgressIndicator()))
        else if (_latest.isEmpty)
          Card(
            child: ListTile(
              leading: const Icon(Icons.monitor_heart_outlined, color: AppColors.primary),
              title: const Text('No measurements yet'),
              trailing: const Icon(Icons.chevron_right),
              onTap: () => widget.onGo(1),
            ),
          )
        else
          GridView.count(
            crossAxisCount: 2,
            shrinkWrap: true,
            physics: const NeverScrollableScrollPhysics(),
            mainAxisSpacing: 12,
            crossAxisSpacing: 12,
            childAspectRatio: 1.25,
            children: [
              for (final code in vitals.keys)
                if (_latest[code] != null)
                  VitalCard(
                      code: code,
                      value: _latest[code]!.value,
                      simulated: _latest[code]!.simulated,
                      caption: ago(_latest[code]!.at)),
            ],
          ),
        const SectionTitle('Quick access'),
        GridView.count(
          crossAxisCount: 3,
          shrinkWrap: true,
          physics: const NeverScrollableScrollPhysics(),
          mainAxisSpacing: 12,
          crossAxisSpacing: 12,
          children: [
            _tile(Icons.biotech, 'Request test', () => widget.onGo(1)),
            _tile(Icons.monitor_heart, 'Results', () => widget.onGo(2)),
            _tile(Icons.sensors, 'Home monitoring', () => _open(const HomeMonitoringScreen())),
            _tile(Icons.smart_toy_outlined, 'Chat assistant', () => _open(const AssistantScreen())),
            _tile(Icons.menu_book, 'Diseases', () => _open(const DiseasesScreen())),
            _tile(Icons.local_hospital, 'Hospitals', () => _open(const NearbyHospitalsScreen())),
            _tile(Icons.history, 'My requests', () => _open(const EmergencyHistoryScreen())),
            _tile(Icons.accessibility_new, 'Accessibility', () => _open(const AccessibilitySettingsScreen())),
          ],
        ),
      ]),
    );
  }

  Widget _sosButton() => Semantics(button: true, label: 'Get emergency help', child: _sosInner());

  Widget _sosInner() => Material(
        color: AppColors.emergency,
        borderRadius: BorderRadius.circular(28),
        child: InkWell(
          borderRadius: BorderRadius.circular(28),
          onTap: () => _open(const EmergencyRequestScreen()),
          child: const Padding(
            padding: EdgeInsets.symmetric(vertical: 28, horizontal: 20),
            child: Row(children: [
              CircleAvatar(radius: 34, backgroundColor: Colors.white24, child: Icon(Icons.emergency, size: 38, color: Colors.white)),
              SizedBox(width: 18),
              Expanded(
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text('SOS', style: TextStyle(color: Colors.white, fontSize: 34, fontWeight: FontWeight.w900)),
                  Text('Request emergency help', style: TextStyle(color: Colors.white70, fontSize: 15)),
                ]),
              ),
              Icon(Icons.chevron_right, color: Colors.white, size: 32),
            ]),
          ),
        ),
      );

  Widget _activeCard(Emergency e) => Card(
        color: const Color(0xFFFEF3F2),
        shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(20), side: const BorderSide(color: AppColors.emergency, width: 1.5)),
        child: ListTile(
          contentPadding: const EdgeInsets.all(16),
          leading: const CircleAvatar(
              backgroundColor: AppColors.emergency, child: Icon(Icons.emergency_share, color: Colors.white)),
          title: Text(e.statusDisplay, style: const TextStyle(fontWeight: FontWeight.w800, fontSize: 18)),
          subtitle: Text(e.ambulancePlate != null
              ? 'Ambulance ${e.ambulancePlate} · ETA ~${e.etaMinutes?.toStringAsFixed(0) ?? '?'} min'
              : 'Active emergency · ${e.typeDisplay}'),
          trailing: const Icon(Icons.chevron_right),
          onTap: () => _open(TrackingScreen(emergencyId: e.id)),
        ),
      );

  Widget _bigAction(IconData icon, String label, Color color, VoidCallback onTap) => Semantics(
        button: true,
        label: label,
        child: Material(
          color: color,
          borderRadius: BorderRadius.circular(22),
          child: InkWell(
            borderRadius: BorderRadius.circular(22),
            onTap: onTap,
            child: SizedBox(
              height: 96,
              child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
                Icon(icon, color: Colors.white, size: 36),
                const SizedBox(height: 4),
                Text(label, style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w800, fontSize: 15)),
              ]),
            ),
          ),
        ),
      );

  Widget _tile(IconData icon, String label, VoidCallback onTap) => Card(
        child: InkWell(
          borderRadius: BorderRadius.circular(20),
          onTap: onTap,
          child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
            Icon(icon, size: 30, color: AppColors.primary),
            const SizedBox(height: 8),
            Text(label, textAlign: TextAlign.center, style: const TextStyle(fontSize: 12.5, fontWeight: FontWeight.w600)),
          ]),
        ),
      );
}
