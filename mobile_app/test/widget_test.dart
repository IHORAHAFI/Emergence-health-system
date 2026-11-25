import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:smart_emergency/screens/login_screen.dart';
import 'package:smart_emergency/theme.dart';
import 'package:smart_emergency/widgets/common.dart';

void main() {
  testWidgets('login screen renders', (tester) async {
    await tester.pumpWidget(MaterialApp(theme: buildTheme(), home: const LoginScreen()));
    expect(find.text('Smart Emergency'), findsOneWidget);
    expect(find.text('Log in'), findsOneWidget);
  });

  testWidgets('simulated values are labelled SIMULATED, real ones REAL DEVICE', (tester) async {
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: Row(children: const [
          Expanded(child: VitalCard(code: 'heart_rate', value: '80', simulated: true)),
          Expanded(child: VitalCard(code: 'spo2', value: '97', simulated: false)),
        ]),
      ),
    ));
    expect(find.text('SIMULATED'), findsOneWidget);
    expect(find.text('REAL DEVICE'), findsOneWidget);
  });
}
