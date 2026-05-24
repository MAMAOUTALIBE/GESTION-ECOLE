// Ecran QR Scan.
//
// Utilise `mobile_scanner` pour ouvrir la camera. A chaque QR detecte,
// debounce (1.5s) puis envoie au backend (ou enqueue offline).
// Affiche un compteur des scans de la session.

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:mobile_scanner/mobile_scanner.dart';

import '../../core/storage/local_storage.dart';
import '../../shared/widgets/offline_banner.dart';
import 'attendance_models.dart';
import 'attendance_service.dart';

final attendanceServiceProvider =
    Provider<AttendanceService>((ref) => AttendanceService());

class ScanScreen extends ConsumerStatefulWidget {
  const ScanScreen({super.key});

  @override
  ConsumerState<ScanScreen> createState() => _ScanScreenState();
}

class _ScanScreenState extends ConsumerState<ScanScreen> {
  final MobileScannerController _controller = MobileScannerController(
    formats: const [BarcodeFormat.qrCode],
    detectionSpeed: DetectionSpeed.normal,
  );

  DateTime _lastScanAt = DateTime.fromMillisecondsSinceEpoch(0);
  String? _lastPayload;
  int _sessionCount = 0;
  bool _processing = false;

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Future<void> _handleDetection(BarcodeCapture capture) async {
    if (_processing) return;
    final code = capture.barcodes.isNotEmpty
        ? capture.barcodes.first.rawValue
        : null;
    if (code == null || code.isEmpty) return;

    final now = DateTime.now();
    final sameAsLast = code == _lastPayload &&
        now.difference(_lastScanAt) < const Duration(seconds: 3);
    if (sameAsLast) return;

    _lastPayload = code;
    _lastScanAt = now;
    setState(() => _processing = true);
    try {
      final service = ref.read(attendanceServiceProvider);
      final result = await service.recordScan(
        AttendanceScan(
          qrPayload: code,
          scannedAt: now.toUtc(),
          schoolId: LocalStorage.getSchoolId(),
        ),
      );
      _sessionCount += 1;
      if (!mounted) return;
      _showFeedback(result);
    } finally {
      if (mounted) setState(() => _processing = false);
    }
  }

  void _showFeedback(AttendanceScanResult result) {
    final color =
        result.queuedOffline ? Colors.orange.shade700 : Colors.green.shade600;
    final text = result.studentName != null
        ? 'Eleve : ${result.studentName}'
        : (result.message ?? (result.queuedOffline
            ? 'Mis en attente'
            : 'Scan envoye'));
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(
        SnackBar(
          backgroundColor: color,
          content: Text(text),
          duration: const Duration(seconds: 2),
        ),
      );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Scan presence'),
        actions: [
          IconButton(
            tooltip: 'Torche',
            icon: const Icon(Icons.flashlight_on),
            onPressed: _controller.toggleTorch,
          ),
          IconButton(
            tooltip: 'Camera avant/arriere',
            icon: const Icon(Icons.cameraswitch),
            onPressed: _controller.switchCamera,
          ),
        ],
      ),
      body: Column(
        children: [
          const OfflineBanner(),
          Expanded(
            child: Stack(
              children: [
                MobileScanner(
                  controller: _controller,
                  onDetect: _handleDetection,
                ),
                Positioned(
                  left: 16,
                  right: 16,
                  bottom: 16,
                  child: Card(
                    color: Colors.black.withOpacity(0.6),
                    child: Padding(
                      padding: const EdgeInsets.all(16),
                      child: Row(
                        children: [
                          const Icon(Icons.qr_code, color: Colors.white),
                          const SizedBox(width: 12),
                          Expanded(
                            child: Text(
                              'Scans cette session : $_sessionCount',
                              style: const TextStyle(color: Colors.white),
                            ),
                          ),
                          if (_processing)
                            const SizedBox(
                              width: 20,
                              height: 20,
                              child: CircularProgressIndicator(
                                strokeWidth: 2,
                                color: Colors.white,
                              ),
                            ),
                        ],
                      ),
                    ),
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
