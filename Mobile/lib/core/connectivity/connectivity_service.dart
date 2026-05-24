// Service de detection connectivite.
//
// Wrapper minimaliste autour de `connectivity_plus`. Expose :
//   - `isOnline()` : check ponctuel
//   - `stream` : changements d'etat
//
// On considere "online" toute connexion != ConnectivityResult.none.

import 'dart:async';

import 'package:connectivity_plus/connectivity_plus.dart';

class ConnectivityService {
  ConnectivityService({Connectivity? connectivity})
      : _connectivity = connectivity ?? Connectivity();

  final Connectivity _connectivity;

  Future<bool> isOnline() async {
    final results = await _connectivity.checkConnectivity();
    return _hasNetwork(results);
  }

  Stream<bool> onStatusChange() {
    return _connectivity.onConnectivityChanged.map(_hasNetwork);
  }

  bool _hasNetwork(List<ConnectivityResult> results) {
    if (results.isEmpty) return false;
    return results.any((r) => r != ConnectivityResult.none);
  }
}
