// Spinner reutilisable.

import 'package:flutter/material.dart';

class LoadingIndicator extends StatelessWidget {
  const LoadingIndicator({super.key, this.size = 24, this.color});

  final double size;
  final Color? color;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: size,
      height: size,
      child: CircularProgressIndicator(
        strokeWidth: size > 24 ? 3 : 2,
        valueColor: color != null
            ? AlwaysStoppedAnimation<Color>(color!)
            : AlwaysStoppedAnimation<Color>(
                Theme.of(context).colorScheme.primary,
              ),
      ),
    );
  }
}
