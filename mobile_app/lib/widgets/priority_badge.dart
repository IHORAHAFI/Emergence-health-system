import 'package:flutter/material.dart';

class PriorityBadge extends StatelessWidget {
  final String priority;
  const PriorityBadge(this.priority, {super.key});

  static Color colorFor(String p) => switch (p) {
        'critical' => Colors.red.shade700,
        'high' => Colors.orange.shade700,
        'medium' => Colors.amber.shade700,
        _ => Colors.green.shade700,
      };

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
        decoration: BoxDecoration(color: colorFor(priority), borderRadius: BorderRadius.circular(12)),
        child: Text(priority.toUpperCase(),
            style: const TextStyle(color: Colors.white, fontWeight: FontWeight.bold, fontSize: 12)),
      );
}
