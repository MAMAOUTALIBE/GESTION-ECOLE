// GESTION-EE Mobile Terrain - Entry point.
//
// Initialise Hive (stockage local offline-first), enregistre les boxes
// necessaires, puis demarre l'application Flutter.

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:hive_flutter/hive_flutter.dart';

import 'app.dart';
import 'core/storage/local_storage.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();

  // Initialise Hive avant tout (les boxes sont utilisees des le splash).
  await Hive.initFlutter();
  await LocalStorage.openAllBoxes();

  runApp(const ProviderScope(child: GestionEeTerrainApp()));
}
