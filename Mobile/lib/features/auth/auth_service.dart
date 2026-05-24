// Service d'authentification.
//
// Encapsule l'appel backend `POST /auth/login`, le parsing, et la persistance
// du token / utilisateur en local.

import 'package:dio/dio.dart';

import '../../core/network/dio_client.dart';
import '../../core/storage/local_storage.dart';
import 'auth_models.dart';

class AuthException implements Exception {
  AuthException(this.message, {this.statusCode});

  final String message;
  final int? statusCode;

  @override
  String toString() => 'AuthException($statusCode): $message';
}

class AuthService {
  AuthService({DioClient? client}) : _client = client ?? DioClient();

  final DioClient _client;

  Future<LoginResponse> login({
    required String username,
    required String password,
  }) async {
    try {
      final response = await _client.post<Map<String, dynamic>>(
        '/auth/login',
        data: LoginRequest(username: username, password: password).toJson(),
      );
      final data = response.data;
      if (data == null) {
        throw AuthException('Reponse vide du serveur');
      }
      final parsed = LoginResponse.fromJson(data);
      await _persist(parsed);
      return parsed;
    } on DioException catch (e) {
      throw AuthException(
        _humanError(e),
        statusCode: e.response?.statusCode,
      );
    }
  }

  Future<void> logout() async {
    await LocalStorage.clearToken();
  }

  bool isAuthenticated() => LocalStorage.getToken() != null;

  Future<void> _persist(LoginResponse response) async {
    await LocalStorage.setToken(response.accessToken);
    final user = response.user;
    if (user != null) {
      await LocalStorage.setUser(user.toJson());
      if (user.schoolId != null) {
        await LocalStorage.setSchoolId(user.schoolId!);
      }
    }
  }

  String _humanError(DioException e) {
    final code = e.response?.statusCode;
    if (code == 401) return 'Identifiants invalides';
    if (code == 403) return 'Acces refuse';
    if (code == 500) return 'Erreur serveur, reessayez plus tard';
    if (e.type == DioExceptionType.connectionTimeout ||
        e.type == DioExceptionType.receiveTimeout) {
      return 'Timeout reseau (2G ?). Reessayez.';
    }
    return 'Connexion impossible';
  }
}
