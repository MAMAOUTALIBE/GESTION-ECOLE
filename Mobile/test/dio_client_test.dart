// Tests : DioClient.
//
// Verifie que l'intercepteur ajoute bien le header `Authorization: Bearer ...`
// quand le token provider renvoie une valeur, et qu'il n'ajoute rien quand
// le token est null/empty.

import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:gestion_ee_terrain/core/network/dio_client.dart';

class _CapturingAdapter implements HttpClientAdapter {
  RequestOptions? lastOptions;

  @override
  void close({bool force = false}) {}

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    lastOptions = options;
    return ResponseBody.fromString(
      '{"ok": true}',
      200,
      headers: {
        Headers.contentTypeHeader: ['application/json'],
      },
    );
  }
}

void main() {
  group('DioClient interceptor', () {
    test('ajoute le header Authorization quand un token est present',
        () async {
      final dio = Dio();
      final adapter = _CapturingAdapter();
      dio.httpClientAdapter = adapter;

      final client = DioClient(
        dio: dio,
        tokenProvider: () => 'super-secret-jwt',
      );

      await client.get<dynamic>('/dashboard');

      final headers = adapter.lastOptions?.headers ?? const {};
      expect(headers['Authorization'], 'Bearer super-secret-jwt');
    });

    test('n\'ajoute pas le header quand le token est null', () async {
      final dio = Dio();
      final adapter = _CapturingAdapter();
      dio.httpClientAdapter = adapter;

      final client = DioClient(
        dio: dio,
        tokenProvider: () => null,
      );

      await client.get<dynamic>('/ping');

      final headers = adapter.lastOptions?.headers ?? const {};
      expect(headers.containsKey('Authorization'), isFalse);
    });

    test('n\'ajoute pas le header quand le token est vide', () async {
      final dio = Dio();
      final adapter = _CapturingAdapter();
      dio.httpClientAdapter = adapter;

      final client = DioClient(
        dio: dio,
        tokenProvider: () => '',
      );

      await client.get<dynamic>('/ping');

      final headers = adapter.lastOptions?.headers ?? const {};
      expect(headers.containsKey('Authorization'), isFalse);
    });
  });
}
