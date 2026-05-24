// Tests : OfflineQueue.
//
// On utilise Hive en mode in-memory (`Hive.init('.')`+ box temporaire) pour
// tester l'enqueue + flush sans toucher au filesystem reel.

import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:hive/hive.dart';

import 'package:gestion_ee_terrain/core/network/dio_client.dart';
import 'package:gestion_ee_terrain/core/network/offline_queue.dart';

class _SuccessAdapter implements HttpClientAdapter {
  int callCount = 0;

  @override
  void close({bool force = false}) {}

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    callCount += 1;
    return ResponseBody.fromString(
      '{"ok": true}',
      200,
      headers: {
        Headers.contentTypeHeader: ['application/json'],
      },
    );
  }
}

class _FailureAdapter implements HttpClientAdapter {
  @override
  void close({bool force = false}) {}

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    return ResponseBody.fromString(
      '{"error": "boom"}',
      500,
      headers: {
        Headers.contentTypeHeader: ['application/json'],
      },
    );
  }
}

Future<Box<dynamic>> _openTempBox(String name) async {
  Hive.init('.test_hive_${DateTime.now().microsecondsSinceEpoch}');
  return Hive.openBox<dynamic>(name);
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('OfflineQueue', () {
    test('enqueue augmente la longueur et persiste la requete', () async {
      final box = await _openTempBox('q_enqueue');
      addTearDown(() async => box.deleteFromDisk());

      final client = DioClient(dio: Dio(), tokenProvider: () => null);
      final queue = OfflineQueue(box: box, client: client);

      expect(queue.isEmpty, isTrue);

      final req = await queue.enqueue(
        method: 'POST',
        path: '/attendance/scan',
        data: {'qr_payload': 'STUDENT-42'},
      );

      expect(queue.length, 1);
      expect(req.method, 'POST');
      expect(req.path, '/attendance/scan');
      expect(req.data?['qr_payload'], 'STUDENT-42');
    });

    test('flush vide la queue quand le serveur repond 200', () async {
      final box = await _openTempBox('q_flush_ok');
      addTearDown(() async => box.deleteFromDisk());

      final dio = Dio();
      final adapter = _SuccessAdapter();
      dio.httpClientAdapter = adapter;
      final client = DioClient(dio: dio, tokenProvider: () => null);
      final queue = OfflineQueue(box: box, client: client);

      await queue.enqueue(method: 'POST', path: '/a', data: {'x': 1});
      await queue.enqueue(method: 'POST', path: '/b', data: {'x': 2});
      expect(queue.length, 2);

      final report = await queue.flush();

      expect(report.success, 2);
      expect(report.failure, 0);
      expect(report.remaining, 0);
      expect(queue.isEmpty, isTrue);
      expect(adapter.callCount, 2);
    });

    test(
        'flush conserve les requetes echouees et incremente le compteur '
        'd\'essais', () async {
      final box = await _openTempBox('q_flush_ko');
      addTearDown(() async => box.deleteFromDisk());

      final dio = Dio();
      dio.httpClientAdapter = _FailureAdapter();
      final client = DioClient(dio: dio, tokenProvider: () => null);
      final queue = OfflineQueue(box: box, client: client);

      await queue.enqueue(method: 'POST', path: '/a', data: {'x': 1});

      final report = await queue.flush();

      expect(report.success, 0);
      expect(report.failure, 1);
      expect(report.remaining, 1);
      expect(queue.snapshot().first.attempts, 1);
      expect(queue.snapshot().first.lastError, contains('500'));
    });
  });
}
