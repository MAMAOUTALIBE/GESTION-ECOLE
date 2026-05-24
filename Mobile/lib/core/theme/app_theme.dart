// Theme global. Reprend la palette du dashboard web (Spruko-like).
//
// Couleurs cles :
//   - Primary  : indigo profond (#5C67F7)
//   - Accent   : turquoise (#26C7FA)
//   - Success  : vert (#1ABC9C)
//   - Warning  : orange (#FFB400)
//   - Danger   : rouge (#E54B4B)
//
// Light + Dark + scale typographique unifies.

import 'package:flutter/material.dart';

class AppTheme {
  AppTheme._();

  static const Color primary = Color(0xFF5C67F7);
  static const Color primaryDark = Color(0xFF3C46D6);
  static const Color accent = Color(0xFF26C7FA);
  static const Color success = Color(0xFF1ABC9C);
  static const Color warning = Color(0xFFFFB400);
  static const Color danger = Color(0xFFE54B4B);
  static const Color neutralDark = Color(0xFF1B2540);
  static const Color neutralLight = Color(0xFFF4F6FB);

  static ThemeData light() {
    final base = ThemeData.light(useMaterial3: true);
    return base.copyWith(
      colorScheme: const ColorScheme.light(
        primary: primary,
        onPrimary: Colors.white,
        secondary: accent,
        onSecondary: Colors.white,
        error: danger,
        surface: Colors.white,
      ),
      scaffoldBackgroundColor: neutralLight,
      appBarTheme: const AppBarTheme(
        backgroundColor: primary,
        foregroundColor: Colors.white,
        elevation: 0,
        centerTitle: false,
      ),
      cardTheme: CardTheme(
        color: Colors.white,
        elevation: 1,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(12),
        ),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          backgroundColor: primary,
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(10),
          ),
          padding: const EdgeInsets.symmetric(vertical: 14, horizontal: 24),
        ),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: Colors.white,
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(10),
          borderSide: const BorderSide(color: Colors.black12),
        ),
      ),
    );
  }

  static ThemeData dark() {
    final base = ThemeData.dark(useMaterial3: true);
    return base.copyWith(
      colorScheme: const ColorScheme.dark(
        primary: primary,
        onPrimary: Colors.white,
        secondary: accent,
        onSecondary: Colors.white,
        error: danger,
        surface: neutralDark,
      ),
      scaffoldBackgroundColor: const Color(0xFF12182B),
      appBarTheme: const AppBarTheme(
        backgroundColor: neutralDark,
        foregroundColor: Colors.white,
        elevation: 0,
        centerTitle: false,
      ),
      cardTheme: CardTheme(
        color: neutralDark,
        elevation: 1,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(12),
        ),
      ),
    );
  }
}
