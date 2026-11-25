import 'package:flutter/material.dart';

import '../services/api_service.dart';
import '../widgets/common.dart';

class ChangePasswordScreen extends StatefulWidget {
  const ChangePasswordScreen({super.key});
  @override
  State<ChangePasswordScreen> createState() => _ChangePasswordScreenState();
}

class _ChangePasswordScreenState extends State<ChangePasswordScreen> {
  final _old = TextEditingController(), _new = TextEditingController(), _confirm = TextEditingController();
  bool _busy = false;

  Future<void> _save() async {
    if (_new.text != _confirm.text) return toast(context, 'New passwords do not match', error: true);
    setState(() => _busy = true);
    try {
      final msg = await ApiService.instance.changePassword(_old.text, _new.text);
      if (!mounted) return;
      toast(context, msg);
      Navigator.pop(context);
    } catch (e) {
      if (mounted) toast(context, e, error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(title: const Text('Change password')),
        body: ListView(padding: const EdgeInsets.all(16), children: [
          TextField(controller: _old, obscureText: true, decoration: const InputDecoration(labelText: 'Current password')),
          const SizedBox(height: 12),
          TextField(controller: _new, obscureText: true, decoration: const InputDecoration(labelText: 'New password (8+ characters)')),
          const SizedBox(height: 12),
          TextField(controller: _confirm, obscureText: true, decoration: const InputDecoration(labelText: 'Confirm new password')),
          const SizedBox(height: 20),
          FilledButton(onPressed: _busy ? null : _save, child: Text(_busy ? 'Saving…' : 'Change password')),
        ]),
      );
}

/// Two steps: ask for a reset e-mail, then enter the two codes from it and a new password.
class ForgotPasswordScreen extends StatefulWidget {
  const ForgotPasswordScreen({super.key});
  @override
  State<ForgotPasswordScreen> createState() => _ForgotPasswordScreenState();
}

class _ForgotPasswordScreenState extends State<ForgotPasswordScreen> {
  final _email = TextEditingController(), _uid = TextEditingController(), _token = TextEditingController();
  final _new = TextEditingController();
  bool _sent = false, _busy = false;

  Future<void> _run(Future<String> Function() f, {bool close = false}) async {
    setState(() => _busy = true);
    try {
      final msg = await f();
      if (!mounted) return;
      toast(context, msg);
      if (close) {
        Navigator.pop(context);
      } else {
        setState(() => _sent = true);
      }
    } catch (e) {
      if (mounted) toast(context, e, error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(title: const Text('Reset password')),
        body: ListView(padding: const EdgeInsets.all(16), children: [
          if (!_sent) ...[
            const Text('Enter the e-mail of your account. We will send you a reset code.'),
            const SizedBox(height: 12),
            TextField(controller: _email, keyboardType: TextInputType.emailAddress,
                decoration: const InputDecoration(labelText: 'E-mail', prefixIcon: Icon(Icons.mail_outline))),
            const SizedBox(height: 16),
            FilledButton(
                onPressed: _busy ? null : () => _run(() => ApiService.instance.requestPasswordReset(_email.text.trim())),
                child: const Text('Send reset code')),
            TextButton(onPressed: () => setState(() => _sent = true), child: const Text('I already have a code')),
          ] else ...[
            const Text('Copy the two codes from the e-mail and choose a new password.'),
            const SizedBox(height: 12),
            TextField(controller: _uid, decoration: const InputDecoration(labelText: 'User ID code')),
            const SizedBox(height: 12),
            TextField(controller: _token, decoration: const InputDecoration(labelText: 'Reset code')),
            const SizedBox(height: 12),
            TextField(controller: _new, obscureText: true, decoration: const InputDecoration(labelText: 'New password (8+ characters)')),
            const SizedBox(height: 16),
            FilledButton(
                onPressed: _busy
                    ? null
                    : () => _run(() => ApiService.instance.confirmPasswordReset(_uid.text.trim(), _token.text.trim(), _new.text),
                        close: true),
                child: const Text('Set new password')),
          ],
        ]),
      );
}
