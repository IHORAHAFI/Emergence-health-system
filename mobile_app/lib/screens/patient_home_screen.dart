import 'package:flutter/material.dart';

import '../models/emergency.dart';
import '../services/api_service.dart';
import '../widgets/priority_badge.dart';
import 'emergency_request_screen.dart';
import 'login_screen.dart';
import 'nearby_hospitals_screen.dart';
import 'tracking_screen.dart';

class PatientHomeScreen extends StatefulWidget {
  const PatientHomeScreen({super.key});
  @override
  State<PatientHomeScreen> createState() => _PatientHomeScreenState();
}

class _PatientHomeScreenState extends State<PatientHomeScreen> {
  late Future<List<Emergency>> _history;

  @override
  void initState() {
    super.initState();
    _reload();
  }

  // Block body: an arrow `() => _history = ...` would return the Future to setState(), which Flutter forbids.
  void _reload() => setState(() {
        _history = ApiService.instance.myEmergencies();
      });

  void _open(Widget w) => Navigator.push(context, MaterialPageRoute(builder: (_) => w)).then((_) => _reload());

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Smart Emergency'), actions: [
        IconButton(
          icon: const Icon(Icons.logout),
          onPressed: () async {
            await ApiService.instance.logout();
            if (!context.mounted) return;
            Navigator.pushReplacement(context, MaterialPageRoute(builder: (_) => const LoginScreen()));
          },
        ),
      ]),
      body: RefreshIndicator(
        onRefresh: () async => _reload(),
        child: ListView(padding: const EdgeInsets.all(16), children: [
          SizedBox(
            height: 180,
            child: FilledButton(
              style: FilledButton.styleFrom(backgroundColor: Colors.red, shape: const CircleBorder()),
              onPressed: () => _open(const EmergencyRequestScreen()),
              child: const Text('SOS', style: TextStyle(fontSize: 48, fontWeight: FontWeight.bold)),
            ),
          ),
          const SizedBox(height: 8),
          const Text('Tap SOS to request emergency help', textAlign: TextAlign.center),
          const SizedBox(height: 16),
          OutlinedButton.icon(
            icon: const Icon(Icons.local_hospital),
            label: const Text('Nearby hospitals'),
            onPressed: () => _open(const NearbyHospitalsScreen()),
          ),
          const Divider(height: 32),
          const Text('My requests', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16)),
          FutureBuilder<List<Emergency>>(
            future: _history,
            builder: (c, s) {
              if (!s.hasData) return const Padding(padding: EdgeInsets.all(16), child: Center(child: CircularProgressIndicator()));
              if (s.data!.isEmpty) return const ListTile(title: Text('No requests yet'));
              return Column(children: [
                for (final e in s.data!)
                  Card(
                    child: ListTile(
                      leading: PriorityBadge(e.priority),
                      title: Text('#${e.id} ${e.typeDisplay}'),
                      subtitle: Text(e.statusDisplay),
                      trailing: e.isActive ? const Icon(Icons.chevron_right) : null,
                      onTap: () => _open(TrackingScreen(emergencyId: e.id)),
                    ),
                  ),
              ]);
            },
          ),
        ]),
      ),
    );
  }
}
