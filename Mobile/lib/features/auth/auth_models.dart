// Modeles d'authentification.
//
// Pas de Freezed pour limiter la dette de build_runner. JSON parsing manuel
// (simple et explicite).

class LoginRequest {
  LoginRequest({required this.username, required this.password});

  final String username;
  final String password;

  Map<String, dynamic> toJson() => {
        'username': username,
        'password': password,
      };
}

class LoginResponse {
  LoginResponse({
    required this.accessToken,
    required this.tokenType,
    this.user,
  });

  factory LoginResponse.fromJson(Map<String, dynamic> json) {
    return LoginResponse(
      accessToken: json['access_token'] as String,
      tokenType: (json['token_type'] as String?) ?? 'bearer',
      user: json['user'] is Map
          ? AuthUser.fromJson(Map<String, dynamic>.from(json['user'] as Map))
          : null,
    );
  }

  final String accessToken;
  final String tokenType;
  final AuthUser? user;
}

class AuthUser {
  AuthUser({
    required this.id,
    required this.username,
    required this.role,
    this.fullName,
    this.schoolId,
  });

  factory AuthUser.fromJson(Map<String, dynamic> json) {
    return AuthUser(
      id: (json['id'] as num).toInt(),
      username: json['username'] as String,
      role: (json['role'] as String?) ?? 'user',
      fullName: json['full_name'] as String?,
      schoolId: json['school_id'] == null
          ? null
          : (json['school_id'] as num).toInt(),
    );
  }

  final int id;
  final String username;
  final String role;
  final String? fullName;
  final int? schoolId;

  Map<String, dynamic> toJson() => {
        'id': id,
        'username': username,
        'role': role,
        'full_name': fullName,
        'school_id': schoolId,
      };
}
