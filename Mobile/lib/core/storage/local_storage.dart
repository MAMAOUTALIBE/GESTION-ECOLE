// Wrapper Hive minimaliste.
//
// Centralise l'ouverture des boxes et expose des helpers typesafe pour
// les acces les plus frequents (token, user, school).

import 'package:hive/hive.dart';

import '../config/env.dart';

class LocalStorage {
  LocalStorage._();

  /// Ouvre toutes les boxes utilisees par l'app. A appeler dans `main()`.
  static Future<void> openAllBoxes() async {
    await Future.wait([
      Hive.openBox<dynamic>(Env.authBoxName),
      Hive.openBox<dynamic>(Env.queueBoxName),
      Hive.openBox<dynamic>(Env.cacheBoxName),
    ]);
  }

  /// Box d'authentification.
  static Box<dynamic> get authBox => Hive.box<dynamic>(Env.authBoxName);

  /// Box de la queue offline.
  static Box<dynamic> get queueBox => Hive.box<dynamic>(Env.queueBoxName);

  /// Box de cache (eleves, ecoles, ...).
  static Box<dynamic> get cacheBox => Hive.box<dynamic>(Env.cacheBoxName);

  // ---------------------------------------------------------------------------
  // Helpers Auth
  // ---------------------------------------------------------------------------

  static String? getToken() => authBox.get(Env.tokenKey) as String?;

  static Future<void> setToken(String token) async {
    await authBox.put(Env.tokenKey, token);
  }

  static Future<void> clearToken() async {
    await authBox.delete(Env.tokenKey);
    await authBox.delete(Env.userKey);
    await authBox.delete(Env.schoolIdKey);
  }

  static Map<String, dynamic>? getUser() {
    final raw = authBox.get(Env.userKey);
    if (raw is Map) {
      return Map<String, dynamic>.from(raw);
    }
    return null;
  }

  static Future<void> setUser(Map<String, dynamic> user) async {
    await authBox.put(Env.userKey, user);
  }

  static int? getSchoolId() => authBox.get(Env.schoolIdKey) as int?;

  static Future<void> setSchoolId(int schoolId) async {
    await authBox.put(Env.schoolIdKey, schoolId);
  }

  // ---------------------------------------------------------------------------
  // Helpers Cache
  // ---------------------------------------------------------------------------

  static List<Map<String, dynamic>> getCachedList(String key) {
    final raw = cacheBox.get(key);
    if (raw is List) {
      return raw
          .whereType<Map>()
          .map((e) => Map<String, dynamic>.from(e))
          .toList();
    }
    return <Map<String, dynamic>>[];
  }

  static Future<void> setCachedList(
    String key,
    List<Map<String, dynamic>> value,
  ) async {
    await cacheBox.put(key, value);
  }

  static Future<void> clearCache() async {
    await cacheBox.clear();
  }
}
