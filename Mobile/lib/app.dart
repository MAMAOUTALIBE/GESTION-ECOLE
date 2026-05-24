// GESTION-EE Mobile Terrain - Racine de l'application.
//
// Definit le router go_router, le theme global, et la MaterialApp.

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import 'core/storage/local_storage.dart';
import 'core/theme/app_theme.dart';
import 'features/attendance/scan_screen.dart';
import 'features/auth/login_screen.dart';
import 'features/dashboard/dashboard_screen.dart';
import 'features/inspections/inspection_form_screen.dart';
import 'features/sync/sync_screen.dart';

/// Provider exposant le router. Recalcule la redirection en fonction du
/// JWT present (ou non) dans Hive.
final routerProvider = Provider<GoRouter>((ref) {
  return GoRouter(
    initialLocation: '/login',
    redirect: (context, state) {
      final isLoggedIn = LocalStorage.getToken() != null;
      final goingToLogin = state.matchedLocation == '/login';
      if (!isLoggedIn && !goingToLogin) {
        return '/login';
      }
      if (isLoggedIn && goingToLogin) {
        return '/dashboard';
      }
      return null;
    },
    routes: [
      GoRoute(
        path: '/login',
        builder: (context, state) => const LoginScreen(),
      ),
      GoRoute(
        path: '/dashboard',
        builder: (context, state) => const DashboardScreen(),
      ),
      GoRoute(
        path: '/scan',
        builder: (context, state) => const ScanScreen(),
      ),
      GoRoute(
        path: '/inspection',
        builder: (context, state) => const InspectionFormScreen(),
      ),
      GoRoute(
        path: '/sync',
        builder: (context, state) => const SyncScreen(),
      ),
    ],
  );
});

class GestionEeTerrainApp extends ConsumerWidget {
  const GestionEeTerrainApp({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final router = ref.watch(routerProvider);
    return MaterialApp.router(
      title: 'GESTION-EE Terrain',
      debugShowCheckedModeBanner: false,
      theme: AppTheme.light(),
      darkTheme: AppTheme.dark(),
      themeMode: ThemeMode.system,
      routerConfig: router,
    );
  }
}
