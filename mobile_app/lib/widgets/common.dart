import 'package:flutter/material.dart';

import '../theme.dart';

/// Display info for each vital sign, keyed by the backend test code.
class VitalMeta {
  final String label, unit;
  final IconData icon;
  final Color color;
  const VitalMeta(this.label, this.unit, this.icon, this.color);
}

const vitals = <String, VitalMeta>{
  'temperature': VitalMeta('Temperature', '°C', Icons.thermostat, Color(0xFFF79009)),
  'heart_rate': VitalMeta('Heart rate', 'BPM', Icons.favorite, Color(0xFFE5484D)),
  'spo2': VitalMeta('Oxygen', '% SpO₂', Icons.air, Color(0xFF2E90FA)),
  'blood_pressure': VitalMeta('Blood pressure', 'mmHg', Icons.speed, Color(0xFF7F56D9)),
  'respiratory': VitalMeta('Breathing', 'breaths/min', Icons.waves, Color(0xFF12B76A)),
};

/// Backend summary keys -> test codes.
const summaryKeyToCode = {
  'temperature_c': 'temperature',
  'heart_rate_bpm': 'heart_rate',
  'spo2_pct': 'spo2',
  'blood_pressure': 'blood_pressure',
  'respiratory_rate': 'respiratory',
};

/// Always shown next to a measurement so a simulated value can never be mistaken for a real one.
class SourceBadge extends StatelessWidget {
  final bool simulated;
  final String source;
  const SourceBadge({super.key, required this.simulated, this.source = 'bluetooth'});

  @override
  Widget build(BuildContext context) {
    final (label, color, icon) = simulated
        ? ('SIMULATED', AppColors.simulated, Icons.science_outlined)
        : source == 'manual'
            ? ('MANUAL', Colors.blueGrey, Icons.edit_outlined)
            : ('REAL DEVICE', AppColors.ok, Icons.bluetooth_connected);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(color: color.withValues(alpha: .12), borderRadius: BorderRadius.circular(20)),
      child: Row(mainAxisSize: MainAxisSize.min, children: [
        Icon(icon, size: 12, color: color),
        const SizedBox(width: 4),
        Text(label, style: TextStyle(fontSize: 10, fontWeight: FontWeight.w700, color: color, letterSpacing: .4)),
      ]),
    );
  }
}

/// Big measurement card used on Home, Results and live test screens.
class VitalCard extends StatelessWidget {
  final String code;
  final String? value;
  final bool simulated;
  final String source;
  final String? caption;
  final bool alert;
  const VitalCard(
      {super.key, required this.code, this.value, this.simulated = false, this.source = 'bluetooth', this.caption, this.alert = false});

  @override
  Widget build(BuildContext context) {
    final m = vitals[code]!;
    return Card(
      shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(20),
          side: BorderSide(color: alert ? AppColors.emergency : const Color(0xFFE4E7EC), width: alert ? 2 : 1)),
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            CircleAvatar(radius: 18, backgroundColor: m.color.withValues(alpha: .14), child: Icon(m.icon, color: m.color, size: 20)),
            const Spacer(),
            if (value != null) SourceBadge(simulated: simulated, source: source),
          ]),
          const Spacer(),
          Text(value ?? '--',
              style: TextStyle(fontSize: 28, fontWeight: FontWeight.w800, color: value == null ? Colors.grey : null)),
          Text('${m.label} · ${m.unit}', style: const TextStyle(fontSize: 12, color: Colors.black54), maxLines: 1,
              overflow: TextOverflow.ellipsis),
          if (caption != null) Text(caption!, style: const TextStyle(fontSize: 11, color: Colors.black45)),
        ]),
      ),
    );
  }
}

Color statusColor(String s) => switch (s) {
      'pending' => AppColors.warn,
      'accepted' || 'assigned' || 'en_route' => AppColors.primary,
      'testing' || 'on_scene' || 'transporting' => const Color(0xFF2E90FA),
      'completed' || 'arrived' => AppColors.ok,
      _ => Colors.grey,
    };

class StatusChip extends StatelessWidget {
  final String status, label;
  const StatusChip(this.status, this.label, {super.key});
  @override
  Widget build(BuildContext context) {
    final c = statusColor(status);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(color: c.withValues(alpha: .12), borderRadius: BorderRadius.circular(20)),
      child: Text(label, style: TextStyle(color: c, fontWeight: FontWeight.w700, fontSize: 12)),
    );
  }
}

class EmptyState extends StatelessWidget {
  final IconData icon;
  final String text;
  final Widget? action;
  const EmptyState(this.icon, this.text, {super.key, this.action});
  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 48, horizontal: 24),
        child: Column(children: [
          Icon(icon, size: 56, color: Colors.black26),
          const SizedBox(height: 12),
          Text(text, textAlign: TextAlign.center, style: const TextStyle(color: Colors.black54, fontSize: 15)),
          if (action != null) ...[const SizedBox(height: 16), action!],
        ]),
      );
}

class ErrorRetry extends StatelessWidget {
  final Object error;
  final VoidCallback onRetry;
  const ErrorRetry(this.error, this.onRetry, {super.key});
  @override
  Widget build(BuildContext context) => EmptyState(Icons.cloud_off, '$error',
      action: OutlinedButton.icon(onPressed: onRetry, icon: const Icon(Icons.refresh), label: const Text('Try again')));
}

class SectionTitle extends StatelessWidget {
  final String text;
  final Widget? trailing;
  const SectionTitle(this.text, {super.key, this.trailing});
  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.fromLTRB(4, 20, 4, 10),
        child: Row(children: [
          Expanded(child: Text(text, style: const TextStyle(fontSize: 17, fontWeight: FontWeight.w700))),
          if (trailing != null) trailing!,
        ]),
      );
}

void toast(BuildContext context, Object msg, {bool error = false}) => ScaffoldMessenger.of(context).showSnackBar(
    SnackBar(content: Text('$msg'), backgroundColor: error ? AppColors.emergency : null, behavior: SnackBarBehavior.floating));

String ago(DateTime t) {
  final d = DateTime.now().difference(t);
  if (d.inMinutes < 1) return 'just now';
  if (d.inHours < 1) return '${d.inMinutes} min ago';
  if (d.inDays < 1) return '${d.inHours} h ago';
  return '${t.day}/${t.month}/${t.year}';
}
