import 'package:flutter/material.dart';

/// Healthcare palette: calm teal for everyday actions, a single strong red reserved for emergencies.
class AppColors {
  static const primary = Color(0xFF0E7C86);
  static const emergency = Color(0xFFD92D20);
  static const ok = Color(0xFF12B76A);
  static const warn = Color(0xFFF79009);
  static const simulated = Color(0xFF7A5AF8); // violet = simulated, never confused with real data
  static const surface = Color(0xFFF4F8F9);
}

/// High contrast (black / yellow, WCAG AAA-level contrast) for patients with low vision.
ThemeData buildHighContrastTheme() {
  const yellow = Color(0xFFFFE600);
  final scheme = const ColorScheme.dark(
      primary: yellow, onPrimary: Colors.black, secondary: yellow, onSecondary: Colors.black,
      surface: Colors.black, onSurface: Colors.white, error: Color(0xFFFF5C5C), onError: Colors.black);
  return ThemeData(
    useMaterial3: true,
    colorScheme: scheme,
    scaffoldBackgroundColor: Colors.black,
    appBarTheme: const AppBarTheme(backgroundColor: Colors.black, foregroundColor: yellow,
        titleTextStyle: TextStyle(fontSize: 24, fontWeight: FontWeight.w800, color: yellow)),
    cardTheme: CardThemeData(color: Colors.black, margin: EdgeInsets.zero,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20), side: const BorderSide(color: yellow, width: 2))),
    filledButtonTheme: FilledButtonThemeData(style: FilledButton.styleFrom(minimumSize: const Size.fromHeight(60),
        backgroundColor: yellow, foregroundColor: Colors.black,
        textStyle: const TextStyle(fontSize: 19, fontWeight: FontWeight.w800))),
    outlinedButtonTheme: OutlinedButtonThemeData(style: OutlinedButton.styleFrom(minimumSize: const Size.fromHeight(56),
        foregroundColor: yellow, side: const BorderSide(color: yellow, width: 2))),
    textTheme: const TextTheme().apply(bodyColor: Colors.white, displayColor: Colors.white),
    navigationBarTheme: NavigationBarThemeData(height: 76, backgroundColor: Colors.black,
        indicatorColor: yellow, iconTheme: WidgetStateProperty.resolveWith((s) =>
            IconThemeData(color: s.contains(WidgetState.selected) ? Colors.black : yellow, size: 28)),
        labelTextStyle: WidgetStateProperty.all(const TextStyle(fontSize: 13, fontWeight: FontWeight.w800, color: yellow))),
  );
}

ThemeData buildTheme() {
  final scheme = ColorScheme.fromSeed(seedColor: AppColors.primary, primary: AppColors.primary, error: AppColors.emergency);
  return ThemeData(
    useMaterial3: true,
    colorScheme: scheme,
    scaffoldBackgroundColor: AppColors.surface,
    appBarTheme: const AppBarTheme(
        backgroundColor: AppColors.surface, elevation: 0, centerTitle: false,
        titleTextStyle: TextStyle(fontSize: 22, fontWeight: FontWeight.w700, color: Color(0xFF101828))),
    cardTheme: CardThemeData(
      elevation: 0,
      color: Colors.white,
      margin: EdgeInsets.zero,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20), side: const BorderSide(color: Color(0xFFE4E7EC))),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
          minimumSize: const Size.fromHeight(56),
          textStyle: const TextStyle(fontSize: 17, fontWeight: FontWeight.w600),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16))),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
          minimumSize: const Size.fromHeight(52),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16))),
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: Colors.white,
      border: OutlineInputBorder(borderRadius: BorderRadius.circular(14)),
      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 16),
    ),
    navigationBarTheme: NavigationBarThemeData(
      height: 70,
      backgroundColor: Colors.white,
      indicatorColor: AppColors.primary.withValues(alpha: .14),
      labelTextStyle: WidgetStateProperty.all(const TextStyle(fontSize: 12, fontWeight: FontWeight.w600)),
    ),
  );
}
