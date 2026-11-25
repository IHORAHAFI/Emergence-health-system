import 'package:flutter/material.dart';
import 'package:flutter/semantics.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:smart_emergency/services/fall_detector.dart';
import 'package:smart_emergency/services/prefs_service.dart';
import 'package:smart_emergency/services/voice_service.dart';
import 'package:smart_emergency/widgets/communication_board.dart';

void main() {
  group('voice intents', () {
    test('emergency words win and are found mid-sentence', () {
      expect(VoiceIntents.parse('please I have chest pain').kind, 'emergency');
      expect(VoiceIntents.parse("I can't breathe").symptoms, contains('cannot_breathe'));
      expect(VoiceIntents.isEmergency('I need emergency'), isTrue);   // partial transcript
      expect(VoiceIntents.parse('au secours').kind, 'emergency');      // French
    });
    test('yes / no / unclear', () {
      expect(VoiceIntents.parse("Yes I'm fine").kind, 'yes');
      expect(VoiceIntents.parse("no, I'm not okay").kind, 'no');      // "okay" inside "not okay" is not a yes
      expect(VoiceIntents.parse('I feel dizzy').kind, 'no');           // a symptom means not okay
      expect(VoiceIntents.parse('').kind, 'unknown');
      expect(VoiceIntents.parse('what is malaria').kind, 'question');  // never read as "I'm fine"
      expect(VoiceIntents.parse('I can breathe fine').kind, 'yes');    // no false "cannot breathe"
    });
    test('commands', () {
      expect(VoiceIntents.parse('check my heart rate').testCode, 'heart_rate');
      expect(VoiceIntents.parse('I need a medical test').kind, 'test');
      expect(VoiceIntents.parse('explain my results').kind, 'results');
      expect(VoiceIntents.parse('I want to talk to a doctor').kind, 'contact');
      expect(VoiceIntents.parse('say that again').kind, 'repeat');
    });
  });

  test('fall detector: free fall -> impact -> still triggers once', () {
    final f = FallDetector.instance;
    var t = DateTime(2026, 1, 1);
    DateTime step(int ms) => t = t.add(Duration(milliseconds: ms));
    expect(f.feed(9.8, step(20)), isFalse);          // normal
    expect(f.feed(1.0, step(20)), isFalse);          // free fall
    expect(f.feed(35.0, step(200)), isFalse);        // impact
    var fired = false;
    for (var i = 0; i < 140; i++) {
      fired = f.feed(9.8, step(20)) || fired;        // lying still ~2.8 s
    }
    expect(fired, isTrue);
    // walking (no free fall) never triggers
    var walk = false;
    for (var i = 0; i < 200; i++) {
      walk = f.feed(i.isEven ? 12.0 : 7.5, step(20)) || walk;
    }
    expect(walk, isFalse);
  });

  test('prefs round trip', () {
    final p = AppPrefs()..apply({'text_scale': 1.5, 'high_contrast': true, 'checkin_times': ['08:00'], 'language': 'fr'});
    expect(p.toJson()['text_scale'], 1.5);
    expect(p.highContrast, isTrue);
    expect(p.checkinTimes, ['08:00']);
    expect(p.language, 'fr');
  });

  testWidgets('communication board: YES, HELP and symptom cards', (tester) async {
    final answers = <BoardAnswer>[];
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: SingleChildScrollView(
        child: CommunicationBoard(onAnswer: answers.add)))));
    await tester.tap(find.text('YES'));
    expect(answers.last.answer, 'ok');
    await tester.tap(find.text('HELP'));
    expect(answers.last.answer, 'help');
    expect(answers.last.symptoms, contains('emergency_help'));
    await tester.tap(find.text('NO'));
    await tester.pump();
    expect(find.text("I can't breathe properly"), findsOneWidget);
    expect(find.bySemanticsLabel('I feel dizzy'), findsOneWidget);    // screen-reader label present
    await tester.tap(find.text("I can't breathe properly"));
    await tester.pump();
    await tester.ensureVisible(find.text('Send'));
    await tester.tap(find.text('Send'));
    expect(answers.last.answer, 'help');                              // severe symptom escalates
  });

  testWidgets('screen reader can activate the big buttons (TalkBack / VoiceOver double-tap)', (tester) async {
    final handle = tester.ensureSemantics();
    final answers = <BoardAnswer>[];
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: SingleChildScrollView(
        child: CommunicationBoard(onAnswer: answers.add)))));
    final yes = tester.getSemantics(find.bySemanticsLabel("Yes, I'm okay"));
    expect(yes.label, "Yes, I'm okay");                              // said once, not twice
    tester.binding.pipelineOwner.semanticsOwner!.performAction(yes.id, SemanticsAction.tap);
    await tester.pump();
    expect(answers.single.answer, 'ok');
    handle.dispose();
  });
}
