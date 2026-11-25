import 'package:flutter/material.dart';

import '../../services/api_service.dart';
import '../../theme.dart';
import '../change_password_screen.dart';
import '../login_screen.dart';

class StaffAccountTab extends StatelessWidget {
  final String role;
  const StaffAccountTab({super.key, required this.role});

  static const roleNames = {
    'operator': 'Emergency operator', 'admin': 'Administrator', 'hospital': 'Healthcare professional',
    'paramedic': 'Ambulance crew', 'caregiver': 'Caregiver',
  };

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<Map<String, dynamic>>(
      future: ApiService.instance.me(),
      builder: (c, s) {
        final me = s.data ?? {};
        final name = '${me['first_name'] ?? ''} ${me['last_name'] ?? ''}'.trim();
        return ListView(padding: const EdgeInsets.fromLTRB(16, 12, 16, 24), children: [
          const Text('Account', style: TextStyle(fontSize: 26, fontWeight: FontWeight.w800)),
          const SizedBox(height: 12),
          Card(
            child: ListTile(
              contentPadding: const EdgeInsets.all(16),
              leading: const CircleAvatar(radius: 26, backgroundColor: AppColors.primary, child: Icon(Icons.badge, color: Colors.white)),
              title: Text(name.isEmpty ? (me['username'] ?? '') : name, style: const TextStyle(fontWeight: FontWeight.w700)),
              subtitle: Text(roleNames[role] ?? role),
            ),
          ),
          const SizedBox(height: 12),
          OutlinedButton.icon(
            onPressed: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const ChangePasswordScreen())),
            icon: const Icon(Icons.lock_reset),
            label: const Text('Change password'),
          ),
          const SizedBox(height: 8),
          OutlinedButton.icon(
            style: OutlinedButton.styleFrom(foregroundColor: AppColors.emergency),
            onPressed: () async {
              await ApiService.instance.logout();
              if (!context.mounted) return;
              Navigator.of(context, rootNavigator: true)
                  .pushAndRemoveUntil(MaterialPageRoute(builder: (_) => const LoginScreen()), (_) => false);
            },
            icon: const Icon(Icons.logout),
            label: const Text('Log out'),
          ),
        ]);
      },
    );
  }
}
