import 'package:flutter/material.dart';

import '../services/api_service.dart';
import '../widgets/common.dart';

class RegisterScreen extends StatefulWidget {
  const RegisterScreen({super.key});
  @override
  State<RegisterScreen> createState() => _RegisterScreenState();
}

class _RegisterScreenState extends State<RegisterScreen> {
  static const labels = {
    'first_name': ('First name', Icons.badge_outlined),
    'last_name': ('Last name', Icons.badge_outlined),
    'username': ('Username', Icons.person_outline),
    'phone': ('Phone', Icons.phone_outlined),
    'email': ('Email (optional)', Icons.mail_outline),
    'password': ('Password (8+ characters)', Icons.lock_outline),
  };
  final _f = {for (final k in labels.keys) k: TextEditingController()};
  final _confirm = TextEditingController();
  String _role = 'patient';
  bool _busy = false;

  Future<void> _submit() async {
    if (_f['username']!.text.trim().isEmpty || _f['password']!.text.length < 8) {
      toast(context, 'Enter a username and a password of at least 8 characters', error: true);
      return;
    }
    if (_f['password']!.text != _confirm.text) {
      toast(context, 'Passwords do not match', error: true);
      return;
    }
    setState(() => _busy = true);
    try {
      await ApiService.instance.register({
        for (final e in _f.entries) e.key: e.key == 'password' ? e.value.text : e.value.text.trim(),
        'role': _role,
      });
      if (!mounted) return;
      toast(context, 'Account created. Please log in.');
      Navigator.pop(context);
    } catch (e) {
      if (mounted) toast(context, e, error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Create account')),
      body: ListView(padding: const EdgeInsets.all(16), children: [
        SegmentedButton<String>(
          segments: const [
            ButtonSegment(value: 'patient', icon: Icon(Icons.person), label: Text('Patient')),
            ButtonSegment(value: 'paramedic', icon: Icon(Icons.local_shipping_outlined), label: Text('Ambulance crew')),
          ],
          selected: {_role},
          onSelectionChanged: (s) => setState(() => _role = s.first),
        ),
        const SizedBox(height: 16),
        for (final e in _f.entries)
          Padding(
            padding: const EdgeInsets.only(bottom: 12),
            child: TextField(
              controller: e.value,
              obscureText: e.key == 'password',
              keyboardType: e.key == 'phone'
                  ? TextInputType.phone
                  : e.key == 'email'
                      ? TextInputType.emailAddress
                      : TextInputType.text,
              decoration: InputDecoration(labelText: labels[e.key]!.$1, prefixIcon: Icon(labels[e.key]!.$2)),
            ),
          ),
        TextField(
          controller: _confirm,
          obscureText: true,
          decoration: const InputDecoration(labelText: 'Confirm password', prefixIcon: Icon(Icons.lock_outline)),
        ),
        const SizedBox(height: 24),
        FilledButton(onPressed: _busy ? null : _submit, child: Text(_busy ? 'Creating…' : 'Create account')),
      ]),
    );
  }
}
