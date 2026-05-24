// Service Sync.
//
// Orchestre la queue offline. Expose un wrapper convenient + signale les
// resultats au screen.

import '../../core/connectivity/connectivity_service.dart';
import '../../core/network/dio_client.dart';
import '../../core/network/offline_queue.dart';
import '../../core/storage/local_storage.dart';

class SyncService {
  SyncService({
    OfflineQueue? queue,
    ConnectivityService? connectivity,
  })  : _queue = queue ??
            OfflineQueue(box: LocalStorage.queueBox, client: DioClient()),
        _connectivity = connectivity ?? ConnectivityService();

  final OfflineQueue _queue;
  final ConnectivityService _connectivity;

  int get pendingCount => _queue.length;

  List<QueuedRequest> pendingRequests() => _queue.snapshot();

  Future<SyncOutcome> sync() async {
    final online = await _connectivity.isOnline();
    if (!online) {
      return SyncOutcome(
        attempted: false,
        message: 'Pas de connexion - synchronisation annulee.',
        report: null,
      );
    }
    final report = await _queue.flush();
    return SyncOutcome(
      attempted: true,
      report: report,
      message: 'Sync terminee : ${report.success} OK, ${report.failure} KO, '
          '${report.remaining} restantes.',
    );
  }

  Future<void> clearAll() => _queue.clear();
}

class SyncOutcome {
  SyncOutcome({
    required this.attempted,
    required this.message,
    required this.report,
  });

  final bool attempted;
  final String message;
  final FlushReport? report;
}
