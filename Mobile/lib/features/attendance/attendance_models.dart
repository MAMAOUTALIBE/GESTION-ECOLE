// Modeles attendance.

class AttendanceScan {
  AttendanceScan({
    required this.qrPayload,
    required this.scannedAt,
    this.schoolId,
    this.notes,
  });

  factory AttendanceScan.fromJson(Map<String, dynamic> json) {
    return AttendanceScan(
      qrPayload: json['qr_payload'] as String,
      scannedAt: DateTime.parse(json['scanned_at'] as String),
      schoolId: json['school_id'] == null
          ? null
          : (json['school_id'] as num).toInt(),
      notes: json['notes'] as String?,
    );
  }

  final String qrPayload;
  final DateTime scannedAt;
  final int? schoolId;
  final String? notes;

  Map<String, dynamic> toJson() => {
        'qr_payload': qrPayload,
        'scanned_at': scannedAt.toUtc().toIso8601String(),
        if (schoolId != null) 'school_id': schoolId,
        if (notes != null) 'notes': notes,
      };
}

class AttendanceScanResult {
  AttendanceScanResult({
    required this.success,
    required this.queuedOffline,
    this.studentName,
    this.message,
  });

  final bool success;
  final bool queuedOffline;
  final String? studentName;
  final String? message;
}
