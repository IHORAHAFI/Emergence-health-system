import 'package:flutter/material.dart';

import '../services/api_service.dart';
import '../theme.dart';
import '../widgets/common.dart';
import 'accessibility_settings_screen.dart';
import 'caregivers_screen.dart';
import 'change_password_screen.dart';
import 'login_screen.dart';

class ProfileTab extends StatefulWidget {
  const ProfileTab({super.key});
  @override
  State<ProfileTab> createState() => _ProfileTabState();
}

class _ProfileTabState extends State<ProfileTab> {
  static const userFields = {'first_name': 'First name', 'last_name': 'Last name', 'phone': 'Phone', 'email': 'Email'};
  static const profileFields = {
    'date_of_birth': 'Date of birth (YYYY-MM-DD)',
    'gender': 'Gender',
    'blood_group': 'Blood group',
    'allergies': 'Allergies',
    'chronic_conditions': 'Chronic conditions',
    'emergency_contact_name': 'Emergency contact name',
    'emergency_contact_phone': 'Emergency contact phone',
    'emergency_contact_relationship': 'Relationship',
    'home_address': 'Home address',
  };

  final _c = {for (final k in [...userFields.keys, ...profileFields.keys]) k: TextEditingController()};
  String _username = '';
  bool _notifyContact = false;
  bool _loading = true, _saving = false, _editing = false;
  Object? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final api = ApiService.instance;
      final me = await api.me();
      final p = await api.profile();
      _username = me['username'] ?? '';
      for (final k in userFields.keys) {
        _c[k]!.text = (me[k] ?? '').toString();
      }
      for (final k in profileFields.keys) {
        _c[k]!.text = (p[k] ?? '').toString();
      }
      _notifyContact = p['notify_emergency_contact'] == true;
    } catch (e) {
      _error = e;
    }
    if (mounted) setState(() => _loading = false);
  }

  Future<void> _save() async {
    setState(() => _saving = true);
    try {
      final api = ApiService.instance;
      await api.updateMe({for (final k in userFields.keys) k: _c[k]!.text.trim()});
      await api.updateProfile({
        for (final k in profileFields.keys)
          k: k == 'date_of_birth' && _c[k]!.text.trim().isEmpty ? null : _c[k]!.text.trim(),
        'notify_emergency_contact': _notifyContact,
      });
      if (!mounted) return;
      setState(() => _editing = false);
      toast(context, 'Profile saved');
    } catch (e) {
      if (mounted) toast(context, e, error: true);
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  Future<void> _logout() async {
    await ApiService.instance.logout();
    if (!mounted) return;
    Navigator.of(context, rootNavigator: true)
        .pushAndRemoveUntil(MaterialPageRoute(builder: (_) => const LoginScreen()), (_) => false);
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) return const Center(child: CircularProgressIndicator());
    if (_error != null) return ErrorRetry(_error!, _load);
    final name = '${_c['first_name']!.text} ${_c['last_name']!.text}'.trim();
    return ListView(padding: const EdgeInsets.fromLTRB(16, 12, 16, 24), children: [
      Row(children: [
        const Expanded(child: Text('Profile', style: TextStyle(fontSize: 26, fontWeight: FontWeight.w800))),
        if (!_editing) IconButton.filledTonal(onPressed: () => setState(() => _editing = true), icon: const Icon(Icons.edit)),
      ]),
      const SizedBox(height: 12),
      Card(
        child: ListTile(
          contentPadding: const EdgeInsets.all(16),
          leading: CircleAvatar(
            radius: 28,
            backgroundColor: AppColors.primary,
            child: Text((name.isEmpty ? _username : name).characters.first.toUpperCase(),
                style: const TextStyle(color: Colors.white, fontSize: 24, fontWeight: FontWeight.w700)),
          ),
          title: Text(name.isEmpty ? _username : name, style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 18)),
          subtitle: Text('@$_username'),
        ),
      ),
      if (!_editing) ...[
        const SectionTitle('Medical'),
        _info(Icons.bloodtype, 'Blood group', _c['blood_group']!.text),
        _info(Icons.warning_amber, 'Allergies', _c['allergies']!.text),
        _info(Icons.medical_information, 'Conditions', _c['chronic_conditions']!.text),
        const SectionTitle('Emergency contact'),
        _info(Icons.contact_phone, _c['emergency_contact_name']!.text.isEmpty ? 'Not set' : _c['emergency_contact_name']!.text,
            _c['emergency_contact_phone']!.text),
        const SizedBox(height: 24),
        OutlinedButton.icon(
          onPressed: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const AccessibilitySettingsScreen())),
          icon: const Icon(Icons.accessibility_new),
          label: const Text('Accessibility, voice & monitoring'),
        ),
        const SizedBox(height: 8),
        OutlinedButton.icon(
          onPressed: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const CaregiversScreen())),
          icon: const Icon(Icons.family_restroom),
          label: const Text('My caregivers'),
        ),
        const SizedBox(height: 8),
        OutlinedButton.icon(
          onPressed: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const ChangePasswordScreen())),
          icon: const Icon(Icons.lock_reset),
          label: const Text('Change password'),
        ),
        const SizedBox(height: 8),
        OutlinedButton.icon(
          onPressed: _logout,
          icon: const Icon(Icons.logout),
          label: const Text('Log out'),
          style: OutlinedButton.styleFrom(foregroundColor: AppColors.emergency),
        ),
      ] else ...[
        const SectionTitle('Personal'),
        for (final e in userFields.entries) _field(e.key, e.value),
        const SectionTitle('Medical & contact'),
        for (final e in profileFields.entries) _field(e.key, e.value),
        SwitchListTile(
          contentPadding: EdgeInsets.zero,
          title: const Text('Text my emergency contact when I request help'),
          value: _notifyContact,
          onChanged: (v) => setState(() => _notifyContact = v),
        ),
        const SizedBox(height: 12),
        FilledButton(onPressed: _saving ? null : _save, child: Text(_saving ? 'Saving…' : 'Save')),
        TextButton(onPressed: () {
          setState(() => _editing = false);
          _load();
        }, child: const Text('Cancel')),
      ],
    ]);
  }

  Widget _info(IconData icon, String label, String value) => Padding(
        padding: const EdgeInsets.only(bottom: 8),
        child: Card(
          child: ListTile(
            leading: Icon(icon, color: AppColors.primary),
            title: Text(label),
            subtitle: Text(value.isEmpty ? '—' : value),
          ),
        ),
      );

  Widget _field(String key, String label) => Padding(
        padding: const EdgeInsets.only(bottom: 12),
        child: TextField(
          controller: _c[key],
          keyboardType: key.contains('phone')
              ? TextInputType.phone
              : key == 'email'
                  ? TextInputType.emailAddress
                  : key == 'date_of_birth'
                      ? TextInputType.datetime
                      : TextInputType.text,
          maxLines: key == 'allergies' || key == 'chronic_conditions' ? 2 : 1,
          decoration: InputDecoration(labelText: label),
        ),
      );
}
