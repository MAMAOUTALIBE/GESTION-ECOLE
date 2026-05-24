// Carte eleve : avatar initiales + nom + classe + sexe.

import 'package:flutter/material.dart';

import '../dashboard_service.dart';

class StudentCard extends StatelessWidget {
  const StudentCard({required this.student, super.key, this.onTap});

  final StudentSummary student;
  final VoidCallback? onTap;

  String get _initials {
    final first = student.firstName.isNotEmpty ? student.firstName[0] : '?';
    final last = student.lastName.isNotEmpty ? student.lastName[0] : '';
    return (first + last).toUpperCase();
  }

  Color _avatarColor(BuildContext context) {
    final base = Theme.of(context).colorScheme.primary;
    final seed = student.id.abs() % 360;
    return HSLColor.fromColor(base).withHue(seed.toDouble()).toColor();
  }

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
      child: ListTile(
        contentPadding:
            const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
        leading: CircleAvatar(
          backgroundColor: _avatarColor(context),
          foregroundColor: Colors.white,
          child: Text(_initials),
        ),
        title: Text(
          student.displayName,
          style: const TextStyle(fontWeight: FontWeight.w600),
        ),
        subtitle: Text(
          [
            if (student.classLabel != null) 'Classe ${student.classLabel}',
            if (student.gender != null) student.gender,
          ].whereType<String>().join(' - '),
        ),
        trailing: const Icon(Icons.chevron_right),
        onTap: onTap,
      ),
    );
  }
}
