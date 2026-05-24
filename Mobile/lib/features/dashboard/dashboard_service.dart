// Service Dashboard.
//
// Recupere les eleves de l'ecole de l'utilisateur courant. En cas d'echec
// reseau, on retourne le cache local Hive si disponible (offline-first).

import 'package:dio/dio.dart';

import '../../core/network/dio_client.dart';
import '../../core/storage/local_storage.dart';

class StudentSummary {
  StudentSummary({
    required this.id,
    required this.firstName,
    required this.lastName,
    this.classLabel,
    this.gender,
    this.birthDate,
  });

  factory StudentSummary.fromJson(Map<String, dynamic> json) {
    return StudentSummary(
      id: (json['id'] as num).toInt(),
      firstName: (json['first_name'] as String?) ?? '',
      lastName: (json['last_name'] as String?) ?? '',
      classLabel: json['class_label'] as String?,
      gender: json['gender'] as String?,
      birthDate: json['birth_date'] as String?,
    );
  }

  final int id;
  final String firstName;
  final String lastName;
  final String? classLabel;
  final String? gender;
  final String? birthDate;

  String get displayName => '$firstName $lastName'.trim();

  Map<String, dynamic> toJson() => {
        'id': id,
        'first_name': firstName,
        'last_name': lastName,
        'class_label': classLabel,
        'gender': gender,
        'birth_date': birthDate,
      };
}

class DashboardService {
  DashboardService({DioClient? client}) : _client = client ?? DioClient();

  final DioClient _client;
  static const String _cacheKey = 'dashboard_students';

  Future<List<StudentSummary>> fetchStudents({int? schoolId}) async {
    final school = schoolId ?? LocalStorage.getSchoolId();
    try {
      final response = await _client.get<dynamic>(
        '/census/students',
        queryParameters: {
          if (school != null) 'school_id': school,
          'limit': 200,
        },
      );
      final data = response.data;
      final list = _extractList(data);
      final parsed =
          list.map((e) => StudentSummary.fromJson(e)).toList(growable: false);

      await LocalStorage.setCachedList(
        _cacheKey,
        parsed.map((e) => e.toJson()).toList(),
      );
      return parsed;
    } on DioException {
      // Fallback cache.
      final cached = LocalStorage.getCachedList(_cacheKey);
      return cached
          .map((e) => StudentSummary.fromJson(e))
          .toList(growable: false);
    }
  }

  List<Map<String, dynamic>> _extractList(dynamic data) {
    if (data is List) {
      return data.whereType<Map>().map((e) {
        return Map<String, dynamic>.from(e);
      }).toList();
    }
    if (data is Map && data['items'] is List) {
      final items = data['items'] as List;
      return items.whereType<Map>().map((e) {
        return Map<String, dynamic>.from(e);
      }).toList();
    }
    return <Map<String, dynamic>>[];
  }
}
