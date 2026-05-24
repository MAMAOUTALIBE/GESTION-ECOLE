// Client Dio centralise.
//
// Configure :
//   - base URL (cf. Env)
//   - timeouts adaptes au 2G
//   - intercepteur JWT (lit le token Hive et l'injecte en `Authorization`)
//   - intercepteur logger (compact, sans bodies pour ne pas spammer)
//
// Le client est volontairement decorrele de Hive en tests : le `tokenProvider`
// est une fonction `String? Function()` pour permettre l'injection.

import 'package:dio/dio.dart';

import '../config/env.dart';
import '../storage/local_storage.dart';

typedef TokenProvider = String? Function();

class DioClient {
  DioClient({
    Dio? dio,
    TokenProvider? tokenProvider,
  })  : _dio = dio ?? Dio(),
        _tokenProvider = tokenProvider ?? LocalStorage.getToken {
    _configure();
  }

  final Dio _dio;
  final TokenProvider _tokenProvider;

  Dio get dio => _dio;

  void _configure() {
    _dio.options
      ..baseUrl = '${Env.apiUrl}${Env.apiPrefix}'
      ..connectTimeout = Duration(seconds: Env.connectTimeoutSeconds)
      ..receiveTimeout = Duration(seconds: Env.receiveTimeoutSeconds)
      ..responseType = ResponseType.json
      ..headers['Content-Type'] = 'application/json'
      ..headers['Accept'] = 'application/json';

    _dio.interceptors.add(
      InterceptorsWrapper(
        onRequest: (options, handler) {
          final token = _tokenProvider();
          if (token != null && token.isNotEmpty) {
            options.headers['Authorization'] = 'Bearer $token';
          }
          handler.next(options);
        },
        onError: (err, handler) {
          // Sur 401, on pourrait deconnecter automatiquement. Pour le MVP
          // on laisse l'erreur remonter au feature.
          handler.next(err);
        },
      ),
    );
  }

  Future<Response<T>> get<T>(
    String path, {
    Map<String, dynamic>? queryParameters,
  }) =>
      _dio.get<T>(path, queryParameters: queryParameters);

  Future<Response<T>> post<T>(
    String path, {
    Object? data,
    Map<String, dynamic>? queryParameters,
  }) =>
      _dio.post<T>(path, data: data, queryParameters: queryParameters);

  Future<Response<T>> put<T>(
    String path, {
    Object? data,
  }) =>
      _dio.put<T>(path, data: data);

  Future<Response<T>> delete<T>(String path) => _dio.delete<T>(path);
}
