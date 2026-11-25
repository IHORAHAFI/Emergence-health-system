import 'package:flutter/material.dart';
import 'package:geolocator/geolocator.dart';

import '../services/api_service.dart';
import '../services/location_service.dart';
import 'tracking_screen.dart';

class EmergencyRequestScreen extends StatefulWidget {
  const EmergencyRequestScreen({super.key});
  @override
  State<EmergencyRequestScreen> createState() => _EmergencyRequestScreenState();
}

class _EmergencyRequestScreenState extends State<EmergencyRequestScreen> {
  static const types = {
    'cardiac': ('Chest pain / heart', Icons.favorite),
    'trauma': ('Accident / injury', Icons.car_crash),
    'respiratory': ('Breathing problem', Icons.air),
    'stroke': ('Stroke signs', Icons.psychology),
    'maternity': ('Pregnancy / labour', Icons.pregnant_woman),
    'burns': ('Burns', Icons.local_fire_department),
    'poisoning': ('Poisoning', Icons.science),
    'other': ('Other', Icons.help),
  };

  String _type = 'other';
  bool _conscious = true, _breathing = false, _bleeding = false, _chest = false, _pregnant = false;
  double _pain = 3;
  final _desc = TextEditingController();
  final _age = TextEditingController();
  Position? _pos;
  String? _locError;
  bool _sending = false;

  @override
  void initState() {
    super.initState();
    LocationService.current()
        .then((p) => setState(() => _pos = p))
        .catchError((e) => setState(() => _locError = e.toString()));
  }

  Future<void> _send() async {
    if (_pos == null) return;
    setState(() => _sending = true);
    try {
      final em = await ApiService.instance.createEmergency({
        'emergency_type': _type,
        'description': _desc.text,
        'latitude': _pos!.latitude,
        'longitude': _pos!.longitude,
        'location_accuracy_m': _pos!.accuracy,
        'conscious': _conscious,
        'breathing_difficulty': _breathing,
        'severe_bleeding': _bleeding,
        'chest_pain': _chest,
        'pain_level': _pain.round(),
        'pregnant': _pregnant,
        if (int.tryParse(_age.text) != null) 'age': int.parse(_age.text),
      });
      if (!mounted) return;
      Navigator.pushReplacement(context, MaterialPageRoute(builder: (_) => TrackingScreen(emergencyId: em.id)));
    } catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Failed: $e')));
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  Widget _q(String label, bool v, ValueChanged<bool> on) => SwitchListTile(title: Text(label), value: v, onChanged: on);

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Request emergency help')),
      body: ListView(padding: const EdgeInsets.all(16), children: [
        Card(
          color: _pos != null ? Colors.green.shade50 : Colors.orange.shade50,
          child: ListTile(
            leading: const Icon(Icons.my_location),
            title: Text(_pos != null
                ? 'Location found (±${_pos!.accuracy.toStringAsFixed(0)} m)'
                : (_locError ?? 'Getting your location…')),
            subtitle: _pos != null ? Text('${_pos!.latitude.toStringAsFixed(5)}, ${_pos!.longitude.toStringAsFixed(5)}') : null,
          ),
        ),
        const SizedBox(height: 8),
        const Text('What is the emergency?', style: TextStyle(fontWeight: FontWeight.bold)),
        Wrap(spacing: 8, children: [
          for (final e in types.entries)
            ChoiceChip(
              avatar: Icon(e.value.$2, size: 18),
              label: Text(e.value.$1),
              selected: _type == e.key,
              onSelected: (_) => setState(() => _type = e.key),
            ),
        ]),
        _q('Patient is conscious', _conscious, (v) => setState(() => _conscious = v)),
        _q('Difficulty breathing', _breathing, (v) => setState(() => _breathing = v)),
        _q('Severe bleeding', _bleeding, (v) => setState(() => _bleeding = v)),
        _q('Chest pain', _chest, (v) => setState(() => _chest = v)),
        _q('Pregnant', _pregnant, (v) => setState(() => _pregnant = v)),
        Text('Pain level: ${_pain.round()}/10'),
        Slider(value: _pain, min: 0, max: 10, divisions: 10, onChanged: (v) => setState(() => _pain = v)),
        TextField(controller: _age, keyboardType: TextInputType.number,
            decoration: const InputDecoration(labelText: 'Patient age (optional)', border: OutlineInputBorder())),
        const SizedBox(height: 12),
        TextField(controller: _desc, maxLines: 3,
            decoration: const InputDecoration(labelText: 'Describe what happened (optional)', border: OutlineInputBorder())),
        const SizedBox(height: 20),
        FilledButton.icon(
          style: FilledButton.styleFrom(backgroundColor: Colors.red, padding: const EdgeInsets.all(18)),
          icon: const Icon(Icons.emergency),
          label: Text(_sending ? 'Sending…' : 'SEND EMERGENCY REQUEST'),
          onPressed: (_pos == null || _sending) ? null : _send,
        ),
        const SizedBox(height: 8),
        const Text('Prototype system - in a real emergency also call 912.',
            textAlign: TextAlign.center, style: TextStyle(color: Colors.grey)),
      ]),
    );
  }
}
