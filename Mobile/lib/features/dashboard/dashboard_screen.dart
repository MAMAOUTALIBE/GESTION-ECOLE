// Ecran Dashboard.
//
// Liste les eleves de l'ecole de l'utilisateur. Drawer avec acces aux
// autres ecrans (scan, inspection, sync, logout).

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/storage/local_storage.dart';
import '../../shared/widgets/error_view.dart';
import '../../shared/widgets/loading_indicator.dart';
import '../../shared/widgets/offline_banner.dart';
import '../auth/login_screen.dart';
import 'dashboard_service.dart';
import 'widgets/student_card.dart';

final dashboardServiceProvider =
    Provider<DashboardService>((ref) => DashboardService());

final studentsProvider = FutureProvider<List<StudentSummary>>((ref) async {
  final service = ref.watch(dashboardServiceProvider);
  return service.fetchStudents();
});

class DashboardScreen extends ConsumerWidget {
  const DashboardScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final asyncStudents = ref.watch(studentsProvider);
    final user = LocalStorage.getUser();
    final fullName =
        user?['full_name'] as String? ?? user?['username'] as String? ?? 'Agent';

    return Scaffold(
      appBar: AppBar(
        title: const Text('Tableau de bord'),
        actions: [
          IconButton(
            tooltip: 'Synchroniser',
            icon: const Icon(Icons.sync),
            onPressed: () => context.push('/sync'),
          ),
          IconButton(
            tooltip: 'Rafraichir',
            icon: const Icon(Icons.refresh),
            onPressed: () => ref.invalidate(studentsProvider),
          ),
        ],
      ),
      drawer: _DashboardDrawer(fullName: fullName, ref: ref),
      body: Column(
        children: [
          const OfflineBanner(),
          Expanded(
            child: asyncStudents.when(
              data: (students) => _StudentList(students: students),
              loading: () => const Center(child: LoadingIndicator()),
              error: (err, _) => ErrorView(
                message: 'Impossible de charger les eleves',
                detail: err.toString(),
                onRetry: () => ref.invalidate(studentsProvider),
              ),
            ),
          ),
        ],
      ),
      floatingActionButton: FloatingActionButton.extended(
        icon: const Icon(Icons.qr_code_scanner),
        label: const Text('Scan presence'),
        onPressed: () => context.push('/scan'),
      ),
    );
  }
}

class _StudentList extends StatelessWidget {
  const _StudentList({required this.students});

  final List<StudentSummary> students;

  @override
  Widget build(BuildContext context) {
    if (students.isEmpty) {
      return const Center(
        child: Padding(
          padding: EdgeInsets.all(24),
          child: Text(
            'Aucun eleve a afficher. Synchronisez ou contactez votre '
            'administrateur.',
            textAlign: TextAlign.center,
          ),
        ),
      );
    }
    return ListView.builder(
      itemCount: students.length,
      itemBuilder: (context, index) => StudentCard(student: students[index]),
    );
  }
}

class _DashboardDrawer extends StatelessWidget {
  const _DashboardDrawer({required this.fullName, required this.ref});

  final String fullName;
  final WidgetRef ref;

  @override
  Widget build(BuildContext context) {
    return Drawer(
      child: SafeArea(
        child: ListView(
          children: [
            UserAccountsDrawerHeader(
              accountName: Text(fullName),
              accountEmail: const Text('Agent terrain'),
              currentAccountPicture: const CircleAvatar(
                child: Icon(Icons.person),
              ),
            ),
            ListTile(
              leading: const Icon(Icons.dashboard),
              title: const Text('Tableau de bord'),
              onTap: () => Navigator.pop(context),
            ),
            ListTile(
              leading: const Icon(Icons.qr_code_scanner),
              title: const Text('Scan presence'),
              onTap: () {
                Navigator.pop(context);
                context.push('/scan');
              },
            ),
            ListTile(
              leading: const Icon(Icons.assignment),
              title: const Text('Nouvelle inspection'),
              onTap: () {
                Navigator.pop(context);
                context.push('/inspection');
              },
            ),
            ListTile(
              leading: const Icon(Icons.sync),
              title: const Text('Synchronisation'),
              onTap: () {
                Navigator.pop(context);
                context.push('/sync');
              },
            ),
            const Divider(),
            ListTile(
              leading: const Icon(Icons.logout),
              title: const Text('Se deconnecter'),
              onTap: () async {
                await ref.read(authServiceProvider).logout();
                if (context.mounted) context.go('/login');
              },
            ),
          ],
        ),
      ),
    );
  }
}
