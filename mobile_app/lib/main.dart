import 'package:flutter/material.dart';

import 'screens/login_screen.dart';
import 'screens/paramedic_home_screen.dart';
import 'screens/patient_shell.dart';
import 'screens/caregiver/caregiver_shell.dart';
import 'screens/staff/staff_shell.dart';
import 'services/local_notify.dart';
import 'services/prefs_service.dart';
import 'services/api_service.dart';
import 'services/measurement_outbox.dart';
import 'theme.dart';

final navigatorKey = GlobalKey<NavigatorState>();

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await ApiService.instance.loadToken();
  await PrefsService.instance.load();
  await LocalNotify.instance.init();
  // Measurements stored while offline (or when the app closed mid-upload) are sent again automatically.
  if (ApiService.instance.isLoggedIn) MeasurementOutbox.instance.resume();
  // Expired session -> back to login from anywhere.
  ApiService.instance.onSessionExpired = () => navigatorKey.currentState
      ?.pushAndRemoveUntil(MaterialPageRoute(builder: (_) => const LoginScreen()), (_) => false);
  runApp(const SmartEmergencyApp());
}

class SmartEmergencyApp extends StatelessWidget {
  const SmartEmergencyApp({super.key});

  @override
  Widget build(BuildContext context) {
    return ValueListenableBuilder(
      valueListenable: PrefsService.instance,
      builder: (context, _, __) => _app(),
    );
  }

  Widget _app() {
    return MaterialApp(
      title: 'Smart Emergency',
      debugShowCheckedModeBanner: false,
      navigatorKey: navigatorKey,
      theme: PrefsService.instance.p.highContrast ? buildHighContrastTheme() : buildTheme(),
      // patient's text-size choice applies everywhere (on top of the phone's own font setting)
      builder: (context, child) {
        final mq = MediaQuery.of(context);
        final scale = PrefsService.instance.p.textScale;
        return MediaQuery(
            data: mq.copyWith(textScaler: TextScaler.linear((mq.textScaler.scale(1) * scale).clamp(0.8, 2.5))),
            child: child!);
      },
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

/// patient -> patient app, paramedic -> crew screen, operator/admin/medical staff -> staff interface.
Widget homeForRole(String? role) => switch (role) {
      'paramedic' => const ParamedicHomeScreen(),
      'operator' || 'admin' || 'hospital' => StaffShell(role: role!),
      'caregiver' => const CaregiverShell(),
      _ => const PatientShell(),
    };
