# GESTION-EE — Mobile Terrain (Flutter MVP scaffold)

App mobile **offline-first** pour les inspecteurs et directeurs d'écoles rurales en Guinée.
Cible : Android 8.0+ (API 26), entrée de gamme, réseau 2G/EDGE intermittent.

> Statut : **Module 17 — scaffold v0.1.0 (MVP)**. Le projet est prêt à être compilé
> sur n'importe quel poste disposant de Flutter 3.24+ : aucun APK n'est versionné.

---

## 1. Prérequis

| Outil | Version minimale | Notes |
|---|---|---|
| Flutter SDK | **3.24.0** (Dart 3.5) | `flutter --version` |
| Android Studio | Jellyfish (2023.3) | + plugin Flutter + plugin Dart |
| Android SDK | API **34** (compile) / **26** (min) | `sdkmanager --install "platforms;android-34"` |
| Java JDK | 17 | distribué avec Android Studio |
| Backend GESTION-EE | branche `main` | doit tourner sur le réseau accessible par le mobile |

```bash
flutter doctor       # doit être tout vert sauf iOS si pas de macOS
flutter pub get      # à la racine de Mobile/
```

> **Pas de Flutter sur le poste de l'agent qui a généré ce scaffold** : la
> vérification statique a été faite à la main. Au premier `flutter pub get`,
> Flutter générera les répertoires `ios/`, `web/`, `linux/`, `macos/`, `windows/`
> manquants si nécessaire (`flutter create . --platforms=android,ios`).

---

## 2. Configuration

L'URL du backend est centralisée dans `lib/core/config/env.dart` et
surchargeable au build :

```bash
# dev local (émulateur Android → backend sur la machine hôte)
flutter run --dart-define=API_URL=http://10.0.2.2:8000

# device physique en LAN
flutter run --dart-define=API_URL=http://192.168.1.42:8000

# production
flutter build apk --release \
  --dart-define=API_URL=https://api.gestionee.gn \
  --dart-define=API_PREFIX=/api
```

Aucun secret n'est versionné — les seules valeurs par défaut pointent vers
le dev local (`http://10.0.2.2:8000`, alias émulateur → host).

---

## 3. Commandes courantes

```bash
# Lancer en debug sur l'appareil connecté
flutter run

# Tests unitaires (pas besoin d'émulateur)
flutter test

# Analyse statique
flutter analyze

# Format
dart format lib/ test/

# Build APK release (split per ABI pour ne pas envoyer 80 Mo)
flutter build apk --release --split-per-abi \
  --dart-define=API_URL=https://api.gestionee.gn

# Le APK est dans build/app/outputs/flutter-apk/
```

Pour générer le projet Android complet (si manquant après clone) :

```bash
flutter create . --platforms=android --org=gn.gouv.gestionee
```

---

## 4. Architecture

```
lib/
├── main.dart                    # init Hive + runApp
├── app.dart                     # MaterialApp + GoRouter
├── core/
│   ├── config/env.dart          # URLs + clés Hive
│   ├── network/dio_client.dart  # Dio + intercepteur JWT
│   ├── network/offline_queue.dart  # file de mutations différées
│   ├── storage/local_storage.dart  # wrapper Hive (auth, cache, queue)
│   ├── theme/app_theme.dart     # palette Spruko + light/dark
│   └── connectivity/connectivity_service.dart  # online/offline detect
├── features/
│   ├── auth/        — login + persistance JWT
│   ├── dashboard/   — liste élèves filtrée par école
│   ├── attendance/  — scanner QR → enqueue ou POST direct
│   ├── inspections/ — formulaire complet (note, photo, GPS)
│   └── sync/        — flush manuel + état de la queue
└── shared/widgets/  — LoadingIndicator, ErrorView, OfflineBanner
```

**Stratégie offline-first.** Chaque mutation passe par
`ConnectivityService` :
- *Online* → POST direct via `DioClient`. En cas d'échec (5xx, timeout 2G,
  reset connexion), la requête est **basculée** dans la queue Hive
  plutôt que d'être perdue.
- *Offline* → Sérialisée immédiatement dans `OfflineQueue` (Hive box
  `offline_queue_box`). Visible et rejouable via l'écran Sync.

**Cache lectures.** `DashboardService.fetchStudents()` met en cache le
résultat en local. Si le device est offline (ou serveur 5xx), on retombe
sur le cache pour ne jamais laisser l'agent avec un écran vide.

**Authentification.** Le JWT est stocké dans la box `auth_box`. Un
intercepteur Dio ajoute le header `Authorization: Bearer <token>` à
chaque requête. Le router go_router redirige vers `/login` si la box
ne contient pas de token.

---

## 5. Tests

3 tests unitaires en pure Dart (zéro émulateur requis) :

| Fichier | Couvre |
|---|---|
| `test/dio_client_test.dart` | intercepteur `Authorization` |
| `test/offline_queue_test.dart` | enqueue, flush 200/500, compteur d'attempts |
| `test/auth_service_test.dart` | parsing `LoginResponse`, mapping 401→message |

```bash
flutter test --reporter expanded
```

---

## 6. Déploiement APK

1. **Signing** : créer `android/key.properties` (jamais commit) :

   ```properties
   storePassword=...
   keyPassword=...
   keyAlias=gestionee-terrain
   storeFile=/abs/path/to/keystore.jks
   ```

2. Référencer ces creds dans `android/app/build.gradle`
   (bloc `signingConfigs.release`) — TODO à compléter au moment du premier
   release.

3. Build :

   ```bash
   flutter build apk --release --split-per-abi
   ```

4. Tester sur device avant publication : `adb install -r <apk>`.

---

## 7. Permissions Android

Déclarées dans `android/app/src/main/AndroidManifest.xml` :
- `INTERNET`, `ACCESS_NETWORK_STATE` (connectivity_plus)
- `CAMERA` (mobile_scanner + image_picker)
- `ACCESS_FINE_LOCATION`, `ACCESS_COARSE_LOCATION` (geolocator)
- `READ_MEDIA_IMAGES` (image_picker pour galerie sur API 33+)

`permission_handler` gère le prompt runtime à la première utilisation.

---

## 8. Roadmap — Module 17.1

Le module 17 livre uniquement le **scaffold démarrable**. À ajouter en 17.1 :

- [ ] **CRDT sync** (Yjs/automerge) pour merger les modifications concurrentes
      sans conflit serveur — actuellement la queue rejoue tel quel
- [ ] **iOS support** : projet Xcode + Podfile + Apple Developer account
- [ ] **OCR cahier de présence** : reconnaître automatiquement les cases
      cochées (tflite + modèle dédié)
- [ ] **Signature électronique** : capture stylet + hash chaîné
- [ ] **Background sync** : `workmanager` plugin, déclencher la flush dès
      retour réseau sans intervention utilisateur
- [ ] **Maps offline** : tuiles MBTiles pour la couverture rurale
- [ ] **Tests intégration** : `flutter_driver` sur émulateur (CI matrix)
- [ ] **Crashlytics / Sentry** : tracking en prod
- [ ] **i18n complet** : `flutter_localizations` + ARB FR/EN/Soussou
- [ ] **Push notifications** : FCM pour rappels d'inspection
- [ ] **Code QR signés** : éviter le replay (HMAC sur QR + horodatage)

---

## 9. Convention de code

- `analysis_options.yaml` + `flutter_lints ^5` (rules strictes : trailing
  commas, const constructors, key in widget constructors, etc.)
- Pas de TODO / FIXME en production
- Pas de `print` (utiliser un logger dédié à venir en 17.1)
- Pas de Freezed pour limiter la dette `build_runner` (parsing JSON manuel
  acceptable au stade MVP)

---

## 10. Sécurité

- HTTPS obligatoire en prod (`API_URL=https://...`)
- `cleartextTraffic=true` activé pour permettre le dev local en HTTP →
  **à désactiver** dans le manifest production
- JWT stocké dans Hive non chiffrée (cf. backlog 17.1 — passer à
  `flutter_secure_storage` + `hive_encryption`)
- Aucun secret committé : pas de clé API, pas de mot de passe par défaut

---

## 11. Support

- Issues : tracker GitHub du repo principal `GESTION-EE`
- Maintainer : équipe produit MEN-GN, contact via `support@gestionee.gn`
