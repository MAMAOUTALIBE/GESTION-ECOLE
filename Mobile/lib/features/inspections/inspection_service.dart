// Service Inspections.
//
// Soumet une inspection au backend. Logique identique a attendance :
// online -> POST direct, offline ou erreur -> enqueue.

import 'package:dio/dio.dart';

import '../../core/connectivity/connectivity_service.dart';
import '../../core/network/dio_client.dart';
import '../../core/network/offline_queue.dart';
import '../../core/storage/local_storage.dart';
import 'inspection_models.dart';

class InspectionSubmitResult {
  InspectionSubmitResult({
    required this.success,
    required this.queuedOffline,
    this.serverId,
    this.message,
  });

  final bool success;
  final bool queuedOffline;
  final int? serverId;
  final String? message;
}

class InspectionService {
  InspectionService({
    DioClient? client,
    OfflineQueue? queue,
    ConnectivityService? connectivity,
  })  : _client = client ?? DioClient(),
        _queue = queue ??
            OfflineQueue(
              box: LocalStorage.queueBox,
              client: client ?? DioClient(),
            ),
        _connectivity = connectivity ?? ConnectivityService();

  final DioClient _client;
  final OfflineQueue _queue;
  final ConnectivityService _connectivity;

  Future<InspectionSubmitResult> submit(InspectionReport report) async {
    final online = await _connectivity.isOnline();
    if (!online) {
      await _queue.enqueue(
        method: 'POST',
        path: '/inspections',
        data: report.toJson(),
      );
      return InspectionSubmitResult(
        success: true,
        queuedOffline: true,
        message: 'Hors ligne : inspection mise en file.',
      );
    }
    try {
      final response = await _client.post<Map<String, dynamic>>(
        '/inspections',
        data: report.toJson(),
      );
      final data = response.data ?? <String, dynamic>{};
      return InspectionSubmitResult(
        success: true,
        queuedOffline: false,
        serverId: data['id'] == null ? null : (data['id'] as num).toInt(),
        message: 'Inspection enregistree',
      );
    } on DioException catch (e) {
      await _queue.enqueue(
        method: 'POST',
        path: '/inspections',
        data: report.toJson(),
      );
      return InspectionSubmitResult(
        success: true,
        queuedOffline: true,
        message:
            'Reseau instable (${e.response?.statusCode ?? 'KO'}), mise en file.',
      );
    }
  }
}
