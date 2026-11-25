import 'package:flutter/material.dart';

import '../services/api_service.dart';

class RegisterScreen extends StatefulWidget {
  const RegisterScreen({super.key});
  @override
  State<RegisterScreen> createState() => _RegisterScreenState();
}

class _RegisterScreenState extends State<RegisterScreen> {
  final _f = {for (final k in ['username', 'first_name', 'last_name', 'phone', 'email', 'password']) k: TextEditingController()};
  String _role = 'patient';
  bool _busy = false;

  Future<void> _submit() async {
    setState(() => _busy = true);
    try {
      await ApiService.instance.register({for (final e in _f.entries) e.key: e.value.text.trim(), 'role': _role});
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Account created. Please login.')));
      Navigator.pop(context);
    } catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Register')),
      body: ListView(padding: const EdgeInsets.all(16), children: [
        for (final e in _f.entries)
          Padding(
            padding: const EdgeInsets.only(bottom: 12),
            child: TextField(
              controller: e.value,
              obscureText: e.key == 'password',
              keyboardType: e.key == 'phone' ? TextInputType.phone : TextInputType.text,
              decoration: InputDecoration(labelText: e.key.replaceAll('_', ' '), border: const OutlineInputBorder()),
            ),
          ),
        DropdownButtonFormField(
          value: _role,
          decoration: const InputDecoration(labelText: 'I am a', border: OutlineInputBorder()),
          items: const [
            DropdownMenuItem(value: 'patient', child: Text('Patient')),
            DropdownMenuItem(value: 'paramedic', child: Text('Ambulance personnel')),
          ],
          onChanged: (v) => setState(() => _role = v!),
        ),
        const SizedBox(height: 20),
        FilledButton(onPressed: _busy ? null : _submit, child: const Text('Create account')),
      ]),
    );
  }
}
