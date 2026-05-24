// Modeles inspection.

class GeoPoint {
  GeoPoint({required this.latitude, required this.longitude, this.accuracy});

  final double latitude;
  final double longitude;
  final double? accuracy;

  Map<String, dynamic> toJson() => {
        'latitude': latitude,
        'longitude': longitude,
        if (accuracy != null) 'accuracy_m': accuracy,
      };
}

class InspectionReport {
  InspectionReport({
    required this.inspectorName,
    required this.observations,
    required this.createdAt,
    this.schoolId,
    this.photoPath,
    this.location,
    this.rating,
  });

  final String inspectorName;
  final String observations;
  final DateTime createdAt;
  final int? schoolId;
  final String? photoPath;
  final GeoPoint? location;
  final int? rating; // 1..5

  Map<String, dynamic> toJson() => {
        'inspector_name': inspectorName,
        'observations': observations,
        'created_at': createdAt.toUtc().toIso8601String(),
        if (schoolId != null) 'school_id': schoolId,
        if (photoPath != null) 'photo_local_path': photoPath,
        if (location != null) 'location': location!.toJson(),
        if (rating != null) 'rating': rating,
      };
}
