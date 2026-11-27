import 'package:flutter/material.dart';

import '../services/measurement_outbox.dart';
import '../theme.dart';

/// Hard limit for every quick measurement, from pressing Start to the result.
const measurementDeadline = Duration(seconds: 60);

enum MeasureOutcome { estimated, measured, failed, inconclusive }

/// The finished result (valid or not) shown to the patient and sent automatically.
class MeasureResult {
  final MeasureOutcome outcome;
  final num? value;
  final String unit;
  final String reason;
  const MeasureResult(this.outcome, {this.value, required this.unit, this.reason = ''});
  bool get valid => outcome == MeasureOutcome.estimated || outcome == MeasureOutcome.measured;
}

/// Big visible countdown: seconds left of the 60-second limit.
class CountdownRing extends StatelessWidget {
  final Duration remaining;
  final String caption;
  const CountdownRing({super.key, required this.remaining, required this.caption});

  @override
  Widget build(BuildContext context) {
    final secs = remaining.inMilliseconds <= 0 ? 0 : (remaining.inMilliseconds / 1000).ceil();
    final frac = remaining.inMilliseconds / measurementDeadline.inMilliseconds;
    final color = secs > 20 ? AppColors.primary : secs > 10 ? AppColors.warn : AppColors.emergency;
    return Semantics(
      label: '$secs seconds left. $caption',
      liveRegion: true,
      child: Column(children: [
        SizedBox(
          width: 150,
          height: 150,
          child: Stack(fit: StackFit.expand, children: [
            CircularProgressIndicator(value: frac.clamp(0.0, 1.0), strokeWidth: 10, color: color,
                backgroundColor: color.withValues(alpha: .15)),
            Center(
              child: Column(mainAxisSize: MainAxisSize.min, children: [
                Text('$secs', style: TextStyle(fontSize: 48, fontWeight: FontWeight.w800, color: color)),
                const Text('seconds left', style: TextStyle(fontSize: 12)),
              ]),
            ),
          ]),
        ),
        const SizedBox(height: 12),
        Text(caption, textAlign: TextAlign.center, style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600)),
      ]),
    );
  }
}

class ResultCard extends StatelessWidget {
  final MeasureResult result;
  final String label;
  final String? note;
  const ResultCard({super.key, required this.result, required this.label, this.note});

  @override
  Widget build(BuildContext context) {
    final (color, tag) = switch (result.outcome) {
      MeasureOutcome.estimated => (AppColors.primary, 'ESTIMATED'),
      MeasureOutcome.measured => (AppColors.ok, 'MEASURED'),
      MeasureOutcome.failed => (AppColors.emergency, 'FAILED'),
      MeasureOutcome.inconclusive => (AppColors.warn, 'INCONCLUSIVE'),
    };
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Text(label, style: const TextStyle(fontWeight: FontWeight.w700)),
            const Spacer(),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
              decoration: BoxDecoration(color: color.withValues(alpha: .12), borderRadius: BorderRadius.circular(99)),
              child: Text(tag, style: TextStyle(color: color, fontWeight: FontWeight.w800, fontSize: 12)),
            ),
          ]),
          const SizedBox(height: 8),
          if (result.valid)
            Text('${result.value} ${result.unit}', style: TextStyle(fontSize: 40, fontWeight: FontWeight.w800, color: color))
          else
            Text(result.reason, style: TextStyle(fontSize: 16, color: color, fontWeight: FontWeight.w600)),
          if (note != null) ...[
            const SizedBox(height: 6),
            Text(note!, style: TextStyle(color: Colors.grey.shade700, fontSize: 13)),
          ],
        ]),
      ),
    );
  }
}

/// Live delivery status. "Sent to operator" only after Django confirmed it saved the measurement.
class SendStatusCard extends StatelessWidget {
  final ValueNotifier<SendState> state;
  const SendStatusCard({super.key, required this.state});

  @override
  Widget build(BuildContext context) => ValueListenableBuilder<SendState>(
        valueListenable: state,
        builder: (context, s, _) {
          final (icon, color, title, sub) = switch (s.phase) {
            SendPhase.sending => (Icons.cloud_upload_outlined, AppColors.primary, 'Sending to operator...', null),
            SendPhase.retrying => (Icons.sync_problem, AppColors.warn, 'Sending failed — retrying (attempt ${s.attempt + 1})',
                '${s.error ?? ''}\nThe result is kept on this phone until the server confirms it.'),
            SendPhase.sent => (Icons.check_circle, AppColors.ok, 'Successfully sent to operator',
                s.dashboardNotified
                    ? 'Saved on the server and pushed to the operator dashboard.'
                    : 'Saved on the server. The operator dashboard shows it on its next refresh (a few seconds).'),
            SendPhase.rejected => (Icons.error, AppColors.emergency, 'The server refused this result', s.error),
          };
          return Semantics(
            liveRegion: true,
            child: Card(
              child: ListTile(
                leading: s.phase == SendPhase.sending || s.phase == SendPhase.retrying
                    ? SizedBox(width: 28, height: 28, child: CircularProgressIndicator(strokeWidth: 3, color: color))
                    : Icon(icon, color: color, size: 30),
                title: Text(title, style: TextStyle(color: color, fontWeight: FontWeight.w700)),
                subtitle: sub == null ? null : Text(sub),
              ),
            ),
          );
        },
      );
}

/// Steps shown before / during a measurement.
class StepLine extends StatelessWidget {
  final String text;
  final bool done;
  final bool active;
  const StepLine(this.text, {super.key, this.done = false, this.active = false});
  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 3),
        child: Row(children: [
          Icon(done ? Icons.check_circle : active ? Icons.radio_button_checked : Icons.radio_button_unchecked,
              size: 18, color: done ? AppColors.ok : active ? AppColors.primary : Colors.grey),
          const SizedBox(width: 8),
          Expanded(child: Text(text, style: TextStyle(fontWeight: active ? FontWeight.w700 : FontWeight.w400))),
        ]),
      );
}
