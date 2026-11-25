import 'package:flutter/material.dart';

import 'screens/login_screen.dart';
import 'screens/paramedic_home_screen.dart';
import 'screens/patient_home_screen.dart';
import 'services/api_service.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await ApiService.instance.loadToken();
  runApp(const SmartEmergencyApp());
}

class SmartEmergencyApp extends StatelessWidget {
  const SmartEmergencyApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Smart Emergency',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(colorSchemeSeed: Colors.red, useMaterial3: true),
      home: const StartupGate(),
    );
  }
}

/// Decides which home screen to show based on the stored login + role.
class StartupGate extends StatelessWidget {
  const StartupGate({super.key});

  @override
  Widget build(BuildContext context) {
    final api = ApiService.instance;
    if (!api.isLoggedIn) return const LoginScreen();
    return FutureBuilder<Map<String, dynamic>>(
      future: api.me(),
      builder: (context, snap) {
        if (snap.connectionState != ConnectionState.done) {
          return const Scaffold(body: Center(child: CircularProgressIndicator()));
        }
        if (snap.hasError) return const LoginScreen();
        return homeForRole(snap.data!['role']);
      },
    );
  }
}

Widget homeForRole(String? role) =>
    role == 'paramedic' ? const ParamedicHomeScreen() : const PatientHomeScreen();
