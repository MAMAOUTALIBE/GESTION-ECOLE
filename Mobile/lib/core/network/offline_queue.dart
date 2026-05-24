// File de requetes offline.
//
// Lorsqu'une mutation (POST/PUT/DELETE) est tentee sans reseau, on l'empile
// dans une box Hive et un sync ulterieur la rejouera dans l'ordre. Les
// entrees gardent timestamp + identifiant local pour traque.
//
// Format d'une entree (stocke en Map<String, dynamic>) :
//   {
//     'id': 'uuid-string',
//     'method': 'POST',
//     'path': '/attendance/scan',
//     'data': { ... },
//     'createdAt': '2026-05-24T12:34:56Z',
//     'attempts': 0,
//     'lastError': null,
//   }

import 'package:dio/dio.dart';
import 'package:hive/hive.dart';

import 'dio_client.dart';

class QueuedRequest {
  QueuedRequest({
    required this.id,
    required this.method,
    required this.path,
    required this.data,
    required this.createdAt,
    this.attempts = 0,
    this.lastError,
  });

  factory QueuedRequest.fromMap(Map<dynamic, dynamic> map) {
    return QueuedRequest(
      id: map['id'] as String,
      method: map['method'] as String,
      path: map['path'] as String,
      data: map['data'] == null
          ? null
          : Map<String, dynamic>.from(map['data'] as Map),
      createdAt: DateTime.parse(map['createdAt'] as String),
      attempts: (map['attempts'] as int?) ?? 0,
      lastError: map['lastError'] as String?,
    );
  }

  final String id;
  final String method;
  final String path;
  final Map<String, dynamic>? data;
  final DateTime createdAt;
  int attempts;
  String? lastError;

  Map<String, dynamic> toMap() => {
        'id': id,
        'method': method,
        'path': path,
        'data': data,
        'createdAt': createdAt.toIso8601String(),
        'attempts': attempts,
        'lastError': lastError,
      };
}

class FlushReport {
  FlushReport({
    required this.success,
    required this.failure,
    required this.remaining,
  });

  final int success;
  final int failure;
  final int remaining;

  bool get hasFailures => failure > 0;

  @override
  String toString() =>
      'FlushReport(success: $success, failure: $failure, remaining: $remaining)';
}

class OfflineQueue {
  OfflineQueue({required Box<dynamic> box, required DioClient client})
      : _box = box,
        _client = client;

  final Box<dynamic> _box;
  final DioClient _client;

  int get length => _box.length;

  bool get isEmpty => _box.isEmpty;

  List<QueuedRequest> snapshot() {
    return _box.values
        .whereType<Map>()
        .map(QueuedRequest.fromMap)
        .toList(growable: false);
  }

  Future<QueuedRequest> enqueue({
    required String method,
    required String path,
    Map<String, dynamic>? data,
  }) async {
    final request = QueuedRequest(
      id: _generateId(),
      method: method.toUpperCase(),
      path: path,
      data: data,
      createdAt: DateTime.now().toUtc(),
    );
    await _box.put(request.id, request.toMap());
    return request;
  }

  Future<void> remove(String id) async {
    await _box.delete(id);
  }

  Future<void> clear() async {
    await _box.clear();
  }

  /// Tente de rejouer toutes les requetes empilees.
  ///
  /// Une requete qui echoue est conservee dans la queue avec
  /// `attempts` incremente et `lastError` mis a jour.
  Future<FlushReport> flush() async {
    var success = 0;
    var failure = 0;

    for (final request in snapshot()) {
      try {
        await _send(request);
        await remove(request.id);
        success++;
      } on DioException catch (e) {
        request.attempts += 1;
        request.lastError = _shortError(e);
        await _box.put(request.id, request.toMap());
        failure++;
      } catch (e) {
        request.attempts += 1;
        request.lastError = e.toString();
        await _box.put(request.id, request.toMap());
        failure++;
      }
    }

    return FlushReport(
      success: success,
      failure: failure,
      remaining: _box.length,
    );
  }

  Future<Response<dynamic>> _send(QueuedRequest req) {
    switch (req.method) {
      case 'POST':
        return _client.post<dynamic>(req.path, data: req.data);
      case 'PUT':
        return _client.put<dynamic>(req.path, data: req.data);
      case 'DELETE':
        return _client.delete<dynamic>(req.path);
      default:
        return _client.get<dynamic>(req.path);
    }
  }

  String _shortError(DioException e) {
    final code = e.response?.statusCode;
    if (code != null) return 'HTTP $code';
    return e.type.name;
  }

  String _generateId() {
    final now = DateTime.now().microsecondsSinceEpoch;
    return 'q_${now.toRadixString(36)}';
  }
}
