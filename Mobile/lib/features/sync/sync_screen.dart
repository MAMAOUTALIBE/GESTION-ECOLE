// Ecran Synchronisation.
//
// Affiche l'etat de la queue offline et un bouton "Synchroniser maintenant".
// Liste les requetes en attente avec leur path + attempts + lastError.

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';

import '../../core/network/offline_queue.dart';
import '../../shared/widgets/loading_indicator.dart';
import '../../shared/widgets/offline_banner.dart';
import 'sync_service.dart';

final syncServiceProvider = Provider<SyncService>((ref) => SyncService());

final pendingCountProvider = StateProvider<int>((ref) {
  return ref.watch(syncServiceProvider).pendingCount;
});

class SyncScreen extends ConsumerStatefulWidget {
  const SyncScreen({super.key});

  @override
  ConsumerState<SyncScreen> createState() => _SyncScreenState();
}

class _SyncScreenState extends ConsumerState<SyncScreen> {
  bool _syncing = false;
  String? _lastMessage;
  late List<QueuedRequest> _items;

  @override
  void initState() {
    super.initState();
    _refreshList();
  }

  void _refreshList() {
    final service = ref.read(syncServiceProvider);
    setState(() => _items = service.pendingRequests());
  }

  Future<void> _runSync() async {
    setState(() => _syncing = true);
    try {
      final service = ref.read(syncServiceProvider);
      final outcome = await service.sync();
      if (!mounted) return;
      _refreshList();
      ref.read(pendingCountProvider.notifier).state = service.pendingCount;
      setState(() => _lastMessage = outcome.message);
    } finally {
      if (mounted) setState(() => _syncing = false);
    }
  }

  Future<void> _clearAll() async {
    final confirm = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Vider la file ?'),
        content: const Text(
          'Toutes les requetes en attente seront perdues sans etre envoyees.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Annuler'),
          ),
          FilledButton.tonal(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Vider'),
          ),
        ],
      ),
    );
    if (confirm == true) {
      await ref.read(syncServiceProvider).clearAll();
      _refreshList();
      ref.read(pendingCountProvider.notifier).state = 0;
      setState(() => _lastMessage = 'File videe.');
    }
  }

  @override
  Widget build(BuildContext context) {
    final df = DateFormat('dd/MM HH:mm');
    return Scaffold(
      appBar: AppBar(
        title: const Text('Synchronisation'),
        actions: [
          IconButton(
            tooltip: 'Vider la file',
            icon: const Icon(Icons.delete_outline),
            onPressed: _items.isEmpty ? null : _clearAll,
          ),
        ],
      ),
      body: Column(
        children: [
          const OfflineBanner(),
          Card(
            margin: const EdgeInsets.all(16),
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      const Icon(Icons.queue),
                      const SizedBox(width: 8),
                      Text(
                        '${_items.length} requete(s) en attente',
                        style: Theme.of(context).textTheme.titleMedium,
                      ),
                    ],
                  ),
                  if (_lastMessage != null) ...[
                    const SizedBox(height: 8),
                    Text(_lastMessage!),
                  ],
                  const SizedBox(height: 16),
                  SizedBox(
                    width: double.infinity,
                    child: FilledButton.icon(
                      icon: _syncing
                          ? const LoadingIndicator(size: 18)
                          : const Icon(Icons.sync),
                      label: const Text('Synchroniser maintenant'),
                      onPressed: _syncing ? null : _runSync,
                    ),
                  ),
                ],
              ),
            ),
          ),
          Expanded(
            child: _items.isEmpty
                ? const Center(child: Text('File vide.'))
                : ListView.separated(
                    itemCount: _items.length,
                    separatorBuilder: (_, __) => const Divider(height: 1),
                    itemBuilder: (context, index) {
                      final item = _items[index];
                      return ListTile(
                        leading: CircleAvatar(
                          backgroundColor: item.lastError == null
                              ? Colors.grey.shade400
                              : Colors.red.shade300,
                          child: Text(item.method[0]),
                        ),
                        title: Text(item.path),
                        subtitle: Text(
                          'Cree ${df.format(item.createdAt.toLocal())} - '
                          'tentatives: ${item.attempts}'
                          '${item.lastError != null ? ' - ${item.lastError}' : ''}',
                        ),
                        isThreeLine: item.lastError != null,
                      );
                    },
                  ),
          ),
        ],
      ),
    );
  }
}
