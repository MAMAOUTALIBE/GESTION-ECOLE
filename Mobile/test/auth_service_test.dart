// Tests : AuthService.
//
// Verifie le parsing de la reponse `/auth/login` et la traduction des
// erreurs Dio en `AuthException` lisible.
//
// On bypass LocalStorage en injectant un DioClient configure avec un
// httpClientAdapter custom. La persistance Hive est testee separement.

import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:hive/hive.dart';

import 'package:gestion_ee_terrain/core/network/dio_client.dart';
import 'package:gestion_ee_terrain/features/auth/auth_models.dart';
import 'package:gestion_ee_terrain/features/auth/auth_service.dart';

class _StaticAdapter implements HttpClientAdapter {
  _StaticAdapter({required this.statusCode, required this.body});

  final int statusCode;
  final String body;

  @override
  void close({bool force = false}) {}

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    return ResponseBody.fromString(
      body,
      statusCode,
      headers: {
        Headers.contentTypeHeader: ['application/json'],
      },
    );
  }
}

Future<void> _openAuthBox(String suffix) async {
  Hive.init('.test_hive_auth_$suffix');
  // open boxes used by LocalStorage helpers indirectly.
  if (!Hive.isBoxOpen('auth_box')) await Hive.openBox<dynamic>('auth_box');
  if (!Hive.isBoxOpen('cache_box')) await Hive.openBox<dynamic>('cache_box');
  if (!Hive.isBoxOpen('offline_queue_box')) {
    await Hive.openBox<dynamic>('offline_queue_box');
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('LoginResponse', () {
    test('parse un payload minimal', () {
      final response = LoginResponse.fromJson({
        'access_token': 'abc.def.ghi',
        'token_type': 'bearer',
      });
      expect(response.accessToken, 'abc.def.ghi');
      expect(response.tokenType, 'bearer');
      expect(response.user, isNull);
    });

    test('parse un payload avec user.school_id', () {
      final response = LoginResponse.fromJson({
        'access_token': 'jwt',
        'token_type': 'bearer',
        'user': {
          'id': 7,
          'username': 'inspector1',
          'role': 'inspector',
          'full_name': 'Mamadou Diallo',
          'school_id': 42,
        },
      });
      expect(response.user, isNotNull);
      expect(response.user!.username, 'inspector1');
      expect(response.user!.schoolId, 42);
      expect(response.user!.fullName, 'Mamadou Diallo');
    });
  });

  group('AuthService.login', () {
    test('parse une reponse 200 et retourne le token', () async {
      await _openAuthBox('200');
      addTearDown(() async {
        await Hive.box<dynamic>('auth_box').clear();
      });

      final dio = Dio();
      dio.httpClientAdapter = _StaticAdapter(
        statusCode: 200,
        body: '{"access_token":"tok","token_type":"bearer",'
            '"user":{"id":1,"username":"u","role":"inspector"}}',
      );
      final client = DioClient(dio: dio, tokenProvider: () => null);
      final service = AuthService(client: client);

      final result = await service.login(username: 'u', password: 'pwd1');

      expect(result.accessToken, 'tok');
      expect(result.user?.username, 'u');
    });

    test('mappe un 401 en AuthException("Identifiants invalides")', () async {
      await _openAuthBox('401');
      addTearDown(() async {
        await Hive.box<dynamic>('auth_box').clear();
      });

      final dio = Dio();
      dio.httpClientAdapter = _StaticAdapter(
        statusCode: 401,
        body: '{"detail":"bad creds"}',
      );
      final client = DioClient(dio: dio, tokenProvider: () => null);
      final service = AuthService(client: client);

      expect(
        () => service.login(username: 'u', password: 'x'),
        throwsA(
          isA<AuthException>()
              .having((e) => e.statusCode, 'statusCode', 401)
              .having((e) => e.message, 'message', contains('invalides')),
        ),
      );
    });
  });
}
