// Bandeau "Hors ligne" visible en haut de chaque ecran principal.
//
// Souscrit a `ConnectivityService.onStatusChange()` et n'affiche rien quand
// le device est online.

import 'package:flutter/material.dart';

import '../../core/connectivity/connectivity_service.dart';

class OfflineBanner extends StatefulWidget {
  const OfflineBanner({super.key, this.connectivity});

  final ConnectivityService? connectivity;

  @override
  State<OfflineBanner> createState() => _OfflineBannerState();
}

class _OfflineBannerState extends State<OfflineBanner> {
  late final ConnectivityService _connectivity =
      widget.connectivity ?? ConnectivityService();
  bool? _online;

  @override
  void initState() {
    super.initState();
    _connectivity.isOnline().then((value) {
      if (mounted) setState(() => _online = value);
    });
  }

  @override
  Widget build(BuildContext context) {
    return StreamBuilder<bool>(
      stream: _connectivity.onStatusChange(),
      initialData: _online,
      builder: (context, snap) {
        final online = snap.data ?? true;
        if (online) return const SizedBox.shrink();
        return Container(
          width: double.infinity,
          color: Colors.orange.shade700,
          padding: const EdgeInsets.symmetric(vertical: 6, horizontal: 12),
          child: const Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Icon(Icons.cloud_off, color: Colors.white, size: 18),
              SizedBox(width: 8),
              Text(
                'Hors ligne - les donnees seront synchronisees plus tard',
                style: TextStyle(color: Colors.white),
              ),
            ],
          ),
        );
      },
    );
  }
}
