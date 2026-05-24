// Service Attendance.
//
// Tente d'envoyer un scan immediatement (online). Si echec reseau ou offline,
// l'enregistrement est ajoute a la queue offline pour rejouage ulterieur.

import 'package:dio/dio.dart';

import '../../core/connectivity/connectivity_service.dart';
import '../../core/network/dio_client.dart';
import '../../core/network/offline_queue.dart';
import '../../core/storage/local_storage.dart';
import 'attendance_models.dart';

class AttendanceService {
  AttendanceService({
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

  Future<AttendanceScanResult> recordScan(AttendanceScan scan) async {
    final online = await _connectivity.isOnline();
    if (!online) {
      await _queue.enqueue(
        method: 'POST',
        path: '/attendance/scan',
        data: scan.toJson(),
      );
      return AttendanceScanResult(
        success: true,
        queuedOffline: true,
        message: 'Hors ligne : scan enregistre, sera envoye a la sync.',
      );
    }

    try {
      final response = await _client.post<Map<String, dynamic>>(
        '/attendance/scan',
        data: scan.toJson(),
      );
      final data = response.data ?? <String, dynamic>{};
      return AttendanceScanResult(
        success: true,
        queuedOffline: false,
        studentName: data['student_name'] as String?,
        message: data['message'] as String?,
      );
    } on DioException catch (e) {
      // Le serveur peut etre injoignable malgre `online` (DNS, 5xx, ...).
      // On enqueue plutot que de perdre le scan.
      await _queue.enqueue(
        method: 'POST',
        path: '/attendance/scan',
        data: scan.toJson(),
      );
      return AttendanceScanResult(
        success: true,
        queuedOffline: true,
        message: 'Reseau instable (${e.response?.statusCode ?? 'KO'}) : '
            'scan mis en attente.',
      );
    }
  }
}
