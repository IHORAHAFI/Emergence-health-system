import 'package:flutter/material.dart';

import '../main.dart';
import '../services/api_service.dart';
import 'register_screen.dart';

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key});
  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final _user = TextEditingController();
  final _pass = TextEditingController();
  bool _busy = false;

  Future<void> _login() async {
    setState(() => _busy = true);
    try {
      await ApiService.instance.login(_user.text.trim(), _pass.text);
      final me = await ApiService.instance.me();
      if (!mounted) return;
      Navigator.pushReplacement(context, MaterialPageRoute(builder: (_) => homeForRole(me['role'])));
    } catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Login failed: $e')));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: ListView(padding: const EdgeInsets.all(24), children: [
          const SizedBox(height: 40),
          const Icon(Icons.local_hospital, size: 80, color: Colors.red),
          const Text('Smart Medical Emergency', textAlign: TextAlign.center,
              style: TextStyle(fontSize: 24, fontWeight: FontWeight.bold)),
          const SizedBox(height: 32),
          TextField(controller: _user, decoration: const InputDecoration(labelText: 'Username', border: OutlineInputBorder())),
          const SizedBox(height: 12),
          TextField(controller: _pass, obscureText: true,
              decoration: const InputDecoration(labelText: 'Password', border: OutlineInputBorder())),
          const SizedBox(height: 20),
          FilledButton(onPressed: _busy ? null : _login,
              child: _busy ? const CircularProgressIndicator() : const Text('Login')),
          TextButton(
              onPressed: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const RegisterScreen())),
              child: const Text('Create an account')),
        ]),
      ),
    );
  }
}
