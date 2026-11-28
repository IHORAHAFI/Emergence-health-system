import 'dart:math' as math;

import 'package:flutter/material.dart';

/// One line of a [SeriesChart]. Points must be in time order (oldest first).
class ChartSeries {
  final String label;
  final Color color;
  final List<(DateTime, double)> points;
  final bool dashed; // dashed = simulated data
  const ChartSeries(this.label, this.color, this.points, {this.dashed = false});
}

/// Small time-series line chart (no extra package): X = time, Y = value with a unit label.
class SeriesChart extends StatelessWidget {
  final List<ChartSeries> series;
  final String unit;
  final double height;
  const SeriesChart({super.key, required this.series, required this.unit, this.height = 150});

  @override
  Widget build(BuildContext context) {
    final pts = series.expand((s) => s.points).toList();
    if (pts.length < 2) {
      return SizedBox(
          height: 48,
          child: Center(child: Text('Not enough history for a chart yet', style: TextStyle(color: Colors.grey.shade600))));
    }
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      SizedBox(height: height, width: double.infinity, child: CustomPaint(painter: _Painter(series, unit))),
      if (series.length > 1)
        Wrap(spacing: 12, children: [
          for (final s in series)
            Row(mainAxisSize: MainAxisSize.min, children: [
              Container(width: 12, height: 3, color: s.color),
              const SizedBox(width: 4),
              Text(s.label, style: const TextStyle(fontSize: 11)),
            ]),
        ]),
    ]);
  }
}

class _Painter extends CustomPainter {
  final List<ChartSeries> series;
  final String unit;
  _Painter(this.series, this.unit);

  @override
  void paint(Canvas canvas, Size size) {
    final all = series.expand((s) => s.points).toList();
    final t0 = all.map((p) => p.$1.millisecondsSinceEpoch).reduce(math.min).toDouble();
    final t1 = all.map((p) => p.$1.millisecondsSinceEpoch).reduce(math.max).toDouble();
    var lo = all.map((p) => p.$2).reduce(math.min), hi = all.map((p) => p.$2).reduce(math.max);
    final pad = math.max((hi - lo) * 0.15, 1.0);
    lo -= pad;
    hi += pad;
    const left = 36.0, bottom = 18.0;
    final w = size.width - left, h = size.height - bottom;
    double x(DateTime t) => left + (t1 == t0 ? w / 2 : (t.millisecondsSinceEpoch - t0) / (t1 - t0) * w);
    double y(double v) => h - (v - lo) / (hi - lo) * h;

    final grid = Paint()..color = Colors.grey.shade300..strokeWidth = 1;
    final tp = TextPainter(textDirection: TextDirection.ltr);
    void label(String s, Offset o) {
      tp.text = TextSpan(text: s, style: TextStyle(fontSize: 10, color: Colors.grey.shade700));
      tp.layout();
      tp.paint(canvas, o);
    }

    for (var i = 0; i <= 3; i++) {
      final v = lo + (hi - lo) * i / 3;
      canvas.drawLine(Offset(left, y(v)), Offset(size.width, y(v)), grid);
      label(v.toStringAsFixed(unit == '°C' ? 1 : 0), Offset(0, y(v) - 6));
    }
    String hm(double ms) {
      final d = DateTime.fromMillisecondsSinceEpoch(ms.round());
      return '${d.hour.toString().padLeft(2, '0')}:${d.minute.toString().padLeft(2, '0')}';
    }

    label(hm(t0), Offset(left, h + 4));
    label(hm(t1), Offset(size.width - 30, h + 4));
    label(unit, const Offset(left + 4, 0));

    for (final s in series) {
      if (s.points.isEmpty) continue;
      final paint = Paint()
        ..color = s.color
        ..strokeWidth = 2
        ..style = PaintingStyle.stroke;
      final path = Path()..moveTo(x(s.points.first.$1), y(s.points.first.$2));
      for (final p in s.points.skip(1)) {
        path.lineTo(x(p.$1), y(p.$2));
      }
      if (s.dashed) {
        for (final m in path.computeMetrics()) {
          for (var d = 0.0; d < m.length; d += 9) {
            canvas.drawPath(m.extractPath(d, math.min(d + 5, m.length)), paint);
          }
        }
      } else {
        canvas.drawPath(path, paint);
      }
      final dot = Paint()..color = s.color;
      for (final p in s.points) {
        canvas.drawCircle(Offset(x(p.$1), y(p.$2)), 2.5, dot);
      }
    }
  }

  @override
  bool shouldRepaint(covariant _Painter old) => true;
}
