// Configuration d'environnement.
//
// Valeurs surchargeables a la compilation via:
//   flutter run --dart-define=API_URL=https://api.gestionee.gn
//
// Les valeurs par defaut sont celles du dev local (Backend FastAPI sur 8000).

class Env {
  Env._();

  /// URL de base du backend GESTION-EE.
  static const String apiUrl = String.fromEnvironment(
    'API_URL',
    defaultValue: 'http://10.0.2.2:8000',
  );

  /// Prefixe API utilise par toutes les routes du backend.
  static const String apiPrefix = String.fromEnvironment(
    'API_PREFIX',
    defaultValue: '/api',
  );

  /// Timeout reseau en secondes (le 2G est lent).
  static const int connectTimeoutSeconds = int.fromEnvironment(
    'CONNECT_TIMEOUT',
    defaultValue: 30,
  );

  static const int receiveTimeoutSeconds = int.fromEnvironment(
    'RECEIVE_TIMEOUT',
    defaultValue: 60,
  );

  /// Cle de la box Hive contenant les credentials.
  static const String authBoxName = 'auth_box';

  /// Cle Hive pour la queue de requetes offline.
  static const String queueBoxName = 'offline_queue_box';

  /// Cle Hive pour le cache des donnees domaine (eleves, ecoles, etc.).
  static const String cacheBoxName = 'cache_box';

  /// Cle JWT stockee dans la box auth.
  static const String tokenKey = 'jwt_token';

  /// Cle utilisateur courant.
  static const String userKey = 'current_user';

  /// Cle ecole de l'utilisateur courant (utilisee pour filtrer les eleves).
  static const String schoolIdKey = 'school_id';

  /// URL complete d'un endpoint relatif (`/auth/login` -> `<api>/auth/login`).
  static String endpoint(String path) => '$apiUrl$apiPrefix$path';
}
