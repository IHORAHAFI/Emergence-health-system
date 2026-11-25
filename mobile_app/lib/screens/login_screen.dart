import 'package:flutter/material.dart';

import '../main.dart';
import '../services/api_service.dart';
import '../theme.dart';
import '../widgets/common.dart';
import 'change_password_screen.dart';
import 'register_screen.dart';

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key});
  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final _user = TextEditingController();
  final _pass = TextEditingController();
  bool _busy = false, _hide = true;

  Future<void> _login() async {
    if (_user.text.trim().isEmpty || _pass.text.isEmpty) return;
    setState(() => _busy = true);
    try {
      await ApiService.instance.login(_user.text.trim(), _pass.text);
      final me = await ApiService.instance.me();
      if (!mounted) return;
      Navigator.pushReplacement(context, MaterialPageRoute(builder: (_) => homeForRole(me['role'])));
    } catch (e) {
      if (mounted) {
        toast(context, e is ApiException && e.status == 401 ? 'Wrong username or password' : e, error: true);
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: ListView(padding: const EdgeInsets.all(24), children: [
          const SizedBox(height: 48),
          Center(
            child: Container(
              padding: const EdgeInsets.all(20),
              decoration: BoxDecoration(color: AppColors.primary.withValues(alpha: .12), shape: BoxShape.circle),
              child: const Icon(Icons.health_and_safety, size: 64, color: AppColors.primary),
            ),
          ),
          const SizedBox(height: 16),
          const Text('Smart Emergency', textAlign: TextAlign.center,
              style: TextStyle(fontSize: 28, fontWeight: FontWeight.w800)),
          const Text('Your health, connected', textAlign: TextAlign.center, style: TextStyle(color: Colors.black54)),
          const SizedBox(height: 36),
          TextField(
            controller: _user,
            textInputAction: TextInputAction.next,
            autofillHints: const [AutofillHints.username],
            decoration: const InputDecoration(labelText: 'Username', prefixIcon: Icon(Icons.person_outline)),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _pass,
            obscureText: _hide,
            autofillHints: const [AutofillHints.password],
            onSubmitted: (_) => _login(),
            decoration: InputDecoration(
              labelText: 'Password',
              prefixIcon: const Icon(Icons.lock_outline),
              suffixIcon: IconButton(
                  icon: Icon(_hide ? Icons.visibility_outlined : Icons.visibility_off_outlined),
                  onPressed: () => setState(() => _hide = !_hide)),
            ),
          ),
          Align(
            alignment: Alignment.centerRight,
            child: TextButton(
                onPressed: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const ForgotPasswordScreen())),
                child: const Text('Forgot password?')),
          ),
          const SizedBox(height: 8),
          FilledButton(
            onPressed: _busy ? null : _login,
            child: _busy
                ? const SizedBox(width: 22, height: 22, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                : const Text('Log in'),
          ),
          const SizedBox(height: 8),
          TextButton(
              onPressed: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const RegisterScreen())),
              child: const Text('New here? Create an account')),
        ]),
      ),
    );
  }
}
