// Ecran formulaire d'inspection.
//
// Champs : nom inspecteur, note 1..5 (etoiles), observations (multiline),
// photo (camera / galerie), GPS (geolocator). Bouton submit -> service.

import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:geolocator/geolocator.dart';
import 'package:image_picker/image_picker.dart';

import '../../core/storage/local_storage.dart';
import '../../shared/widgets/loading_indicator.dart';
import '../../shared/widgets/offline_banner.dart';
import 'inspection_models.dart';
import 'inspection_service.dart';

final inspectionServiceProvider =
    Provider<InspectionService>((ref) => InspectionService());

class InspectionFormScreen extends ConsumerStatefulWidget {
  const InspectionFormScreen({super.key});

  @override
  ConsumerState<InspectionFormScreen> createState() =>
      _InspectionFormScreenState();
}

class _InspectionFormScreenState extends ConsumerState<InspectionFormScreen> {
  final _formKey = GlobalKey<FormState>();
  final _nameCtrl = TextEditingController();
  final _observationsCtrl = TextEditingController();
  final ImagePicker _picker = ImagePicker();

  int _rating = 3;
  File? _photo;
  GeoPoint? _location;
  bool _submitting = false;
  bool _capturingGps = false;

  @override
  void initState() {
    super.initState();
    final user = LocalStorage.getUser();
    if (user != null) {
      _nameCtrl.text =
          (user['full_name'] as String?) ?? (user['username'] as String? ?? '');
    }
  }

  @override
  void dispose() {
    _nameCtrl.dispose();
    _observationsCtrl.dispose();
    super.dispose();
  }

  Future<void> _pickPhoto({required bool fromCamera}) async {
    final picked = await _picker.pickImage(
      source: fromCamera ? ImageSource.camera : ImageSource.gallery,
      imageQuality: 60,
      maxWidth: 1280,
    );
    if (picked != null) {
      setState(() => _photo = File(picked.path));
    }
  }

  Future<void> _captureGps() async {
    setState(() => _capturingGps = true);
    try {
      final permission = await Geolocator.requestPermission();
      if (permission == LocationPermission.denied ||
          permission == LocationPermission.deniedForever) {
        throw Exception('Permission GPS refusee');
      }
      final position = await Geolocator.getCurrentPosition(
        locationSettings: const LocationSettings(
          accuracy: LocationAccuracy.high,
        ),
      );
      setState(() {
        _location = GeoPoint(
          latitude: position.latitude,
          longitude: position.longitude,
          accuracy: position.accuracy,
        );
      });
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('GPS indisponible : $e')),
      );
    } finally {
      if (mounted) setState(() => _capturingGps = false);
    }
  }

  Future<void> _submit() async {
    if (!_formKey.currentState!.validate()) return;
    setState(() => _submitting = true);
    try {
      final report = InspectionReport(
        inspectorName: _nameCtrl.text.trim(),
        observations: _observationsCtrl.text.trim(),
        createdAt: DateTime.now().toUtc(),
        schoolId: LocalStorage.getSchoolId(),
        photoPath: _photo?.path,
        location: _location,
        rating: _rating,
      );
      final service = ref.read(inspectionServiceProvider);
      final result = await service.submit(report);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          backgroundColor: result.queuedOffline
              ? Colors.orange.shade700
              : Colors.green.shade600,
          content: Text(result.message ?? 'OK'),
        ),
      );
      // Reset partiel : on garde le nom inspecteur, on vide le reste.
      _observationsCtrl.clear();
      setState(() {
        _photo = null;
        _location = null;
        _rating = 3;
      });
    } finally {
      if (mounted) setState(() => _submitting = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Nouvelle inspection')),
      body: Column(
        children: [
          const OfflineBanner(),
          Expanded(
            child: SingleChildScrollView(
              padding: const EdgeInsets.all(16),
              child: Form(
                key: _formKey,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    TextFormField(
                      controller: _nameCtrl,
                      decoration: const InputDecoration(
                        labelText: 'Nom de l\'inspecteur',
                        prefixIcon: Icon(Icons.badge_outlined),
                      ),
                      validator: (v) =>
                          (v == null || v.trim().isEmpty) ? 'Requis' : null,
                    ),
                    const SizedBox(height: 16),
                    Card(
                      child: Padding(
                        padding: const EdgeInsets.all(16),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            const Text('Note generale'),
                            const SizedBox(height: 8),
                            Row(
                              mainAxisAlignment: MainAxisAlignment.center,
                              children: List.generate(5, (i) {
                                final filled = i < _rating;
                                return IconButton(
                                  iconSize: 32,
                                  icon: Icon(
                                    filled ? Icons.star : Icons.star_border,
                                    color: filled
                                        ? Colors.amber.shade700
                                        : Colors.grey,
                                  ),
                                  onPressed: () =>
                                      setState(() => _rating = i + 1),
                                );
                              }),
                            ),
                          ],
                        ),
                      ),
                    ),
                    const SizedBox(height: 16),
                    TextFormField(
                      controller: _observationsCtrl,
                      maxLines: 6,
                      decoration: const InputDecoration(
                        labelText: 'Observations',
                        alignLabelWithHint: true,
                      ),
                      validator: (v) => (v == null || v.trim().length < 5)
                          ? 'Minimum 5 caracteres'
                          : null,
                    ),
                    const SizedBox(height: 16),
                    _PhotoSection(
                      photo: _photo,
                      onCamera: () => _pickPhoto(fromCamera: true),
                      onGallery: () => _pickPhoto(fromCamera: false),
                      onClear: () => setState(() => _photo = null),
                    ),
                    const SizedBox(height: 16),
                    _GpsSection(
                      location: _location,
                      capturing: _capturingGps,
                      onCapture: _captureGps,
                    ),
                    const SizedBox(height: 24),
                    FilledButton.icon(
                      onPressed: _submitting ? null : _submit,
                      icon: _submitting
                          ? const LoadingIndicator(size: 18)
                          : const Icon(Icons.cloud_upload),
                      label: const Text('Soumettre'),
                    ),
                    const SizedBox(height: 32),
                  ],
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _PhotoSection extends StatelessWidget {
  const _PhotoSection({
    required this.photo,
    required this.onCamera,
    required this.onGallery,
    required this.onClear,
  });

  final File? photo;
  final VoidCallback onCamera;
  final VoidCallback onGallery;
  final VoidCallback onClear;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text('Photo (optionnelle)'),
            const SizedBox(height: 8),
            if (photo != null)
              Stack(
                children: [
                  ClipRRect(
                    borderRadius: BorderRadius.circular(8),
                    child: Image.file(photo!, height: 160, fit: BoxFit.cover),
                  ),
                  Positioned(
                    right: 4,
                    top: 4,
                    child: IconButton.filledTonal(
                      icon: const Icon(Icons.close),
                      onPressed: onClear,
                    ),
                  ),
                ],
              )
            else
              Row(
                children: [
                  Expanded(
                    child: OutlinedButton.icon(
                      onPressed: onCamera,
                      icon: const Icon(Icons.camera_alt),
                      label: const Text('Camera'),
                    ),
                  ),
                  const SizedBox(width: 8),
                  Expanded(
                    child: OutlinedButton.icon(
                      onPressed: onGallery,
                      icon: const Icon(Icons.photo_library),
                      label: const Text('Galerie'),
                    ),
                  ),
                ],
              ),
          ],
        ),
      ),
    );
  }
}

class _GpsSection extends StatelessWidget {
  const _GpsSection({
    required this.location,
    required this.capturing,
    required this.onCapture,
  });

  final GeoPoint? location;
  final bool capturing;
  final VoidCallback onCapture;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Row(
          children: [
            const Icon(Icons.gps_fixed),
            const SizedBox(width: 12),
            Expanded(
              child: location == null
                  ? const Text('GPS non capture')
                  : Text(
                      'Lat: ${location!.latitude.toStringAsFixed(5)}\n'
                      'Lon: ${location!.longitude.toStringAsFixed(5)}',
                    ),
            ),
            FilledButton.tonal(
              onPressed: capturing ? null : onCapture,
              child: capturing
                  ? const LoadingIndicator(size: 16)
                  : const Text('Capturer'),
            ),
          ],
        ),
      ),
    );
  }
}
