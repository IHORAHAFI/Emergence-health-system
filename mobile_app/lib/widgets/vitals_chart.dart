import 'package:flutter/material.dart';

import '../models/vital_reading.dart';
import '../theme.dart';
import 'common.dart';

/// Line chart of one vital sign over time (no extra package). REAL points are solid green,
/// SIMULATED points are hollow violet, so the two can never be mistaken for each other.
class VitalsChart extends StatefulWidget {
  final List<VitalReading> readings; // any order
  const VitalsChart({super.key, required this.readings});
  @override
  State<VitalsChart> createState() => _VitalsChartState();
}

double? valueOf(VitalReading r, String code) => switch (code) {
      'temperature' => r.temperature,
      'heart_rate' => r.heartRate?.toDouble(),
      'spo2' => r.spo2?.toDouble(),
      'blood_pressure' => r.systolic?.toDouble(),
      'respiratory' => r.respiratoryRate?.toDouble(),
      _ => null,
    };

class _VitalsChartState extends State<VitalsChart> {
  String? _code;

  @override
  Widget build(BuildContext context) {
    final available = [for (final c in vitals.keys) if (widget.readings.any((r) => valueOf(r, c) != null)) c];
    if (available.isEmpty) return const SizedBox.shrink();
    final code = available.contains(_code) ? _code! : available.first;
    final pts = widget.readings.where((r) => valueOf(r, code) != null).toList()
      ..sort((a, b) => a.measuredAt.compareTo(b.measuredAt));
    final m = vitals[code]!;
    return Card(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(12, 10, 12, 12),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: Row(children: [
              for (final c in available)
                Padding(
                  padding: const EdgeInsets.only(right: 6),
                  child: ChoiceChip(
                    avatar: Icon(vitals[c]!.icon, size: 16, color: vitals[c]!.color),
                    label: Text(vitals[c]!.label),
                    selected: c == code,
                    onSelected: (_) => setState(() => _code = c),
                  ),
                ),
            ]),
          ),
          const SizedBox(height: 8),
          Text('${m.label} (${code == 'blood_pressure' ? 'systolic, mmHg' : m.unit}) · ${pts.length} readings',
              style: const TextStyle(fontSize: 12, color: Colors.black54)),
          const SizedBox(height: 6),
          SizedBox(height: 160, width: double.infinity, child: CustomPaint(painter: _LinePainter(pts, code, m.color))),
          const SizedBox(height: 6),
          const Row(children: [
            Icon(Icons.circle, size: 10, color: AppColors.ok),
            Text(' Real device   ', style: TextStyle(fontSize: 11)),
            Icon(Icons.circle_outlined, size: 10, color: AppColors.simulated),
            Text(' Simulated', style: TextStyle(fontSize: 11)),
          ]),
        ]),
      ),
    );
  }
}

class _LinePainter extends CustomPainter {
  final List<VitalReading> pts;
  final String code;
  final Color color;
  _LinePainter(this.pts, this.code, this.color);

  @override
  void paint(Canvas canvas, Size size) {
    if (pts.isEmpty) return;
    final vs = [for (final p in pts) valueOf(p, code)!];
    var lo = vs.reduce((a, b) => a < b ? a : b), hi = vs.reduce((a, b) => a > b ? a : b);
    if (hi - lo < 1) {
      lo -= 1;
      hi += 1;
    }
    const left = 34.0, bottom = 6.0;
    final w = size.width - left, h = size.height - bottom;
    final t0 = pts.first.measuredAt.millisecondsSinceEpoch.toDouble();
    final span = (pts.last.measuredAt.millisecondsSinceEpoch - t0).toDouble();
    Offset at(int i) => Offset(left + (span == 0 ? w / 2 : (pts[i].measuredAt.millisecondsSinceEpoch - t0) / span * w),
        h - (vs[i] - lo) / (hi - lo) * h);

    final grid = Paint()..color = const Color(0xFFE4E7EC)..strokeWidth = 1;
    final tp = TextPainter(textDirection: TextDirection.ltr);
    for (final f in [0.0, 0.5, 1.0]) {
      final y = h - f * h;
      canvas.drawLine(Offset(left, y), Offset(size.width, y), grid);
      tp.text = TextSpan(text: (lo + f * (hi - lo)).toStringAsFixed(code == 'temperature' ? 1 : 0),
          style: const TextStyle(fontSize: 10, color: Colors.black45));
      tp.layout();
      tp.paint(canvas, Offset(0, y - tp.height / 2));
    }
    final line = Paint()
      ..color = color
      ..strokeWidth = 2
      ..style = PaintingStyle.stroke;
    final path = Path()..moveTo(at(0).dx, at(0).dy);
    for (var i = 1; i < pts.length; i++) {
      path.lineTo(at(i).dx, at(i).dy);
    }
    canvas.drawPath(path, line);
    for (var i = 0; i < pts.length; i++) {
      final sim = pts[i].simulated;
      canvas.drawCircle(at(i), 4, Paint()..color = sim ? Colors.white : AppColors.ok);
      canvas.drawCircle(at(i), 4, Paint()
        ..color = sim ? AppColors.simulated : AppColors.ok
        ..style = PaintingStyle.stroke
        ..strokeWidth = 2);
    }
  }

  @override
  bool shouldRepaint(covariant _LinePainter old) => old.pts != pts || old.code != code;
}
