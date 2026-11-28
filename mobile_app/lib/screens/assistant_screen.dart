import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../services/api_service.dart';
import '../services/voice_service.dart';
import '../theme.dart';
import 'diseases_screen.dart';
import 'emergency_request_screen.dart';
import 'heart_rate_screen.dart';
import 'nearby_hospitals_screen.dart';
import 'vital_signs_screen.dart';
import 'voice_assistant_screen.dart';

/// Chat with the backend's grounded medical assistant (answers come only from its reviewed knowledge base).
class AssistantScreen extends StatefulWidget {
  const AssistantScreen({super.key});
  @override
  State<AssistantScreen> createState() => _AssistantScreenState();
}

class _Msg {
  final String text;
  final bool mine;
  final Map<String, dynamic>? data;
  _Msg(this.text, this.mine, [this.data]);
}

class _AssistantScreenState extends State<AssistantScreen> {
  static const suggestions = [
    'What are the signs of a stroke?',
    'How do I treat a burn?',
    'My child has diarrhoea',
    'Someone is choking',
  ];
  final _q = TextEditingController();
  final _scroll = ScrollController();
  final _msgs = <_Msg>[];
  bool _busy = false;
  int? _conversationId;
  bool _save = true, _useVitals = false, _isPatient = false;
  bool _listening = false;
  _Msg? _speaking; // the answer being read aloud
  final _rated = <String, String>{}; // response_id -> rating given

  @override
  void initState() {
    super.initState();
    ApiService.instance.me().then((m) => mounted ? setState(() => _isPatient = m['role'] == 'patient') : null).catchError((_) {});
  }

  @override
  void dispose() {
    VoiceService.instance.stopSpeaking();
    VoiceService.instance.stop();
    super.dispose();
  }

  /// Speak instead of typing. The recognised words are put in the text box first. A clear result is sent;
  /// a low-confidence one is NOT sent - the patient checks / corrects it (never pretend unclear audio was understood).
  Future<void> _mic() async {
    final v = VoiceService.instance;
    if (_listening) {
      await v.stop();
      return;
    }
    await v.stopSpeaking();
    setState(() => _listening = true);
    final text = await v.listenOnce(onPartial: (p) => _q.text = p);
    if (!mounted) return;
    setState(() => _listening = false);
    if (text.trim().isEmpty) {
      _snack(v.lastError != null && v.lastError!.contains('permission')
          ? 'Microphone permission is off. Allow it in the phone settings to speak to the assistant.'
          : "I didn't hear anything. Please try again closer to the phone, or type your question.");
      return;
    }
    _q.text = text;
    final c = v.lastConfidence;
    if (c != null && c > 0 && c < VoiceService.minConfidence) {
      _snack("I'm not sure I heard that correctly. Please check the text, correct it, then press send.");
      return;
    }
    _ask();
  }

  Future<void> _speak(_Msg m) async {
    setState(() => _speaking = m);
    await VoiceService.instance.say(m.text, force: true); // the patient pressed Listen: always speak
    if (mounted && _speaking == m) setState(() => _speaking = null);
  }

  Future<void> _stopSpeaking({bool pause = false}) async {
    pause ? await VoiceService.instance.pauseSpeaking() : await VoiceService.instance.stopSpeaking();
    if (mounted) setState(() => _speaking = null);
  }

  Future<void> _rate(Map<String, dynamic> d, String rating) async {
    final id = d['response_id'] as String?;
    if (id == null || _rated.containsKey(id)) return;
    setState(() => _rated[id] = rating);
    try {
      await ApiService.instance.assistantFeedback(d, rating);
    } catch (_) {/* optional */}
  }

  /// Suggested next steps open existing screens; nothing is executed automatically.
  Future<void> _suggestion(Map<String, dynamic> d, Map<String, dynamic> s) async {
    if (d['response_id'] != null) {
      ApiService.instance.assistantFeedback(d, 'suggestion', suggestion: s['id']).catchError((_) {});
    }
    Widget? screen = switch (s['action']) {
      'emergency' => const EmergencyRequestScreen(),
      'heart_rate_test' => const HeartRateScreen(),
      'vital_signs' => const VitalSignsScreen(),
      'voice' => const VoiceAssistantScreen(),
      'hospitals' => const NearbyHospitalsScreen(),
      _ => null,
    };
    if (s['action'] == 'call_hotline') {
      await launchUrl(Uri.parse('tel:912'));
      return;
    }
    if (!mounted) return;
    if (screen != null) {
      Navigator.push(context, MaterialPageRoute(builder: (_) => screen));
    } else {
      _snack('${s['label']}: open it from the home screen.');
    }
  }

  void _snack(String t) {
    if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(t)));
  }

  void _newChat() => setState(() {
        _msgs.clear();
        _conversationId = null;
      });

  Future<void> _history() async {
    List<Map<String, dynamic>> items;
    try {
      items = await ApiService.instance.assistantHistory();
    } catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
      return;
    }
    if (!mounted) return;
    await showModalBottomSheet(
      context: context,
      showDragHandle: true,
      builder: (c) => SafeArea(
        child: ListView(shrinkWrap: true, padding: const EdgeInsets.fromLTRB(16, 0, 16, 16), children: [
          Row(children: [
            const Expanded(child: Text('My conversations', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700))),
            if (items.isNotEmpty)
              TextButton(
                onPressed: () async {
                  await ApiService.instance.deleteAssistantHistory();
                  if (c.mounted) Navigator.pop(c);
                  _newChat();
                },
                child: const Text('Delete all', style: TextStyle(color: AppColors.emergency)),
              ),
          ]),
          const Text('Only you can see these. They are deleted automatically after 30 days.',
              style: TextStyle(fontSize: 12, color: Colors.black54)),
          if (items.isEmpty) const Padding(padding: EdgeInsets.all(16), child: Text('No saved conversations.')),
          for (final it in items)
            ListTile(
              contentPadding: EdgeInsets.zero,
              title: Text(it['title'] ?? 'Conversation', maxLines: 1, overflow: TextOverflow.ellipsis),
              onTap: () async {
                Navigator.pop(c);
                final d = await ApiService.instance.assistantConversation(it['id']);
                setState(() {
                  _conversationId = d['id'];
                  _msgs
                    ..clear()
                    ..addAll([
                      for (final m in (d['messages'] as List))
                        _Msg(m['text'] ?? '', m['role'] == 'user', m['role'] == 'user' ? null : Map<String, dynamic>.from(m['meta'] ?? {}))
                    ]);
                });
              },
              trailing: IconButton(
                icon: const Icon(Icons.delete_outline),
                onPressed: () async {
                  await ApiService.instance.deleteAssistantHistory(it['id']);
                  if (c.mounted) Navigator.pop(c);
                  if (_conversationId == it['id']) _newChat();
                },
              ),
            ),
        ]),
      ),
    );
  }

  Future<void> _ask([String? text]) async {
    final q = (text ?? _q.text).trim();
    if (q.isEmpty || _busy) return;
    _q.clear();
    setState(() {
      _msgs.add(_Msg(q, true));
      _busy = true;
    });
    try {
      final r = await ApiService.instance.ask(q,
          conversationId: _conversationId, saveHistory: _save, includeVitals: _isPatient && _useVitals);
      _conversationId = r['conversation_id'] ?? _conversationId;
      _msgs.add(_Msg(r['ok'] == true ? r['answer'] ?? '' : (r['error'] ?? 'No answer found.'), false, r));
    } catch (e) {
      _msgs.add(_Msg('$e', false));
    }
    if (!mounted) return;
    setState(() => _busy = false);
    WidgetsBinding.instance.addPostFrameCallback(
        (_) => _scroll.hasClients ? _scroll.animateTo(_scroll.position.maxScrollExtent, duration: const Duration(milliseconds: 250), curve: Curves.easeOut) : null);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('AI assistant'), actions: [
        IconButton(tooltip: 'My conversations', onPressed: _history, icon: const Icon(Icons.history)),
        IconButton(tooltip: 'New conversation', onPressed: _newChat, icon: const Icon(Icons.add_comment_outlined)),
      ]),
      body: Column(children: [
        Expanded(
          child: _msgs.isEmpty
              ? ListView(padding: const EdgeInsets.all(16), children: [
                  const Icon(Icons.smart_toy_outlined, size: 64, color: AppColors.primary),
                  const SizedBox(height: 8),
                  const Text('Ask a health question', textAlign: TextAlign.center,
                      style: TextStyle(fontSize: 20, fontWeight: FontWeight.w700)),
                  const SizedBox(height: 16),
                  for (final s in suggestions)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 8),
                      child: OutlinedButton(onPressed: () => _ask(s), child: Text(s)),
                    ),
                ])
              : ListView.builder(
                  controller: _scroll,
                  padding: const EdgeInsets.all(16),
                  itemCount: _msgs.length + (_busy ? 1 : 0),
                  itemBuilder: (c, i) => i == _msgs.length ? _typing() : _bubble(_msgs[i]),
                ),
        ),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 8),
          child: Wrap(spacing: 4, children: [
            if (_isPatient)
              FilterChip(
                avatar: const Icon(Icons.monitor_heart_outlined, size: 18),
                label: const Text('Use my latest vitals'),
                selected: _useVitals,
                onSelected: (v) => setState(() => _useVitals = v),
              ),
            FilterChip(
              avatar: const Icon(Icons.history, size: 18),
              label: const Text('Save history'),
              selected: _save,
              onSelected: (v) => setState(() => _save = v),
            ),
          ]),
        ),
        SafeArea(
          top: false,
          child: Padding(
            padding: const EdgeInsets.fromLTRB(12, 4, 12, 8),
            child: Row(children: [
              Expanded(
                child: TextField(
                  controller: _q,
                  textInputAction: TextInputAction.send,
                  onSubmitted: (_) => _ask(),
                  decoration: InputDecoration(hintText: _listening ? 'Listening... speak now' : 'Type or speak your question'),
                ),
              ),
              const SizedBox(width: 4),
              IconButton(
                tooltip: _listening ? 'Stop listening' : 'Speak instead of typing',
                onPressed: _busy ? null : _mic,
                icon: Icon(_listening ? Icons.mic_off : Icons.mic, color: _listening ? AppColors.emergency : null),
                iconSize: 28,
              ),
              IconButton.filled(
                  onPressed: _busy ? null : _ask, icon: const Icon(Icons.send), iconSize: 26, padding: const EdgeInsets.all(14)),
            ]),
          ),
        ),
      ]),
    );
  }

  /// Listen / pause / stop (replay = Listen again) and the feedback buttons under each answer.
  Widget _controls(_Msg m) {
    final d = m.data;
    final speaking = _speaking == m;
    final id = d?['response_id'] as String?;
    final rated = id == null ? null : _rated[id];
    return Padding(
      padding: const EdgeInsets.only(top: 4),
      child: Wrap(crossAxisAlignment: WrapCrossAlignment.center, children: [
        if (!speaking)
          IconButton(tooltip: 'Listen (replay)', onPressed: () => _speak(m), icon: const Icon(Icons.volume_up, size: 20)),
        if (speaking) ...[
          IconButton(tooltip: 'Pause', onPressed: () => _stopSpeaking(pause: true), icon: const Icon(Icons.pause, size: 20)),
          IconButton(tooltip: 'Stop', onPressed: _stopSpeaking, icon: const Icon(Icons.stop, size: 20)),
        ],
        if (id != null && rated == null) ...[
          IconButton(tooltip: 'Helpful', onPressed: () => _rate(d!, 'helpful'), icon: const Icon(Icons.thumb_up_outlined, size: 20)),
          IconButton(tooltip: 'Not helpful', onPressed: () => _rate(d!, 'unhelpful'), icon: const Icon(Icons.thumb_down_outlined, size: 20)),
          TextButton(onPressed: () => _rate(d!, 'unclear'), child: const Text('Unclear')),
        ],
        if (rated != null) const Text('Thanks for the feedback', style: TextStyle(fontSize: 11, color: Colors.black45)),
      ]),
    );
  }

  Widget _typing() => const Align(
      alignment: Alignment.centerLeft,
      child: Padding(padding: EdgeInsets.all(12), child: SizedBox(width: 24, height: 24, child: CircularProgressIndicator(strokeWidth: 2))));

  Widget _bubble(_Msg m) {
    final d = m.data;
    final library = List<Map<String, dynamic>>.from((d?['library'] ?? const []).map((e) => Map<String, dynamic>.from(e)));
    final sources = List<Map<String, dynamic>>.from((d?['sources'] ?? const []).map((e) => Map<String, dynamic>.from(e)));
    return Align(
      alignment: m.mine ? Alignment.centerRight : Alignment.centerLeft,
      child: Container(
        constraints: BoxConstraints(maxWidth: MediaQuery.of(context).size.width * .85),
        margin: const EdgeInsets.only(bottom: 10),
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(
          color: m.mine ? AppColors.primary : Colors.white,
          borderRadius: BorderRadius.circular(18),
          border: m.mine ? null : Border.all(color: const Color(0xFFE4E7EC)),
        ),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          if (d?['emergency'] == true) ...[
            FilledButton.icon(
              style: FilledButton.styleFrom(backgroundColor: AppColors.emergency, minimumSize: const Size.fromHeight(46)),
              onPressed: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const EmergencyRequestScreen())),
              icon: const Icon(Icons.emergency),
              label: const Text('This may be an emergency - get help'),
            ),
            const SizedBox(height: 10),
          ],
          Text(m.text, style: TextStyle(color: m.mine ? Colors.white : null, fontSize: 15, height: 1.4)),
          if (d?['used_vitals'] == true)
            const Padding(
              padding: EdgeInsets.only(top: 6),
              child: Text('Your latest recorded vital signs were used.', style: TextStyle(fontSize: 11, color: Colors.black45)),
            ),
          if (library.isNotEmpty) ...[
            const SizedBox(height: 10),
            Wrap(spacing: 6, runSpacing: 6, children: [
              for (final e in library)
                ActionChip(
                  avatar: const Icon(Icons.menu_book, size: 16),
                  label: Text(e['name']),
                  onPressed: () => Navigator.push(
                      context, MaterialPageRoute(builder: (_) => DiseaseDetailScreen(slug: e['slug']))),
                ),
            ]),
          ],
          if (sources.isNotEmpty) ...[
            const SizedBox(height: 8),
            Text('Sources: ${sources.map((s) => s['source']).toSet().join(', ')}',
                style: const TextStyle(fontSize: 11, color: Colors.black45)),
          ],
          if (d != null && (d['suggestions'] as List?)?.isNotEmpty == true) ...[
            const SizedBox(height: 10),
            Wrap(spacing: 6, runSpacing: 6, children: [
              for (final s in (d['suggestions'] as List).map((e) => Map<String, dynamic>.from(e)))
                Tooltip(
                  message: s['reason'] ?? '',
                  child: ActionChip(
                    backgroundColor: s['pinned'] == true ? AppColors.emergency.withValues(alpha: 0.12) : null,
                    avatar: Icon(s['pinned'] == true ? Icons.emergency : Icons.arrow_forward, size: 16),
                    label: Text(s['label'] ?? ''),
                    onPressed: () => _suggestion(d, s),
                  ),
                ),
            ]),
          ],
          if (d?['disclaimer'] != null && d?['mode'] != 'conversation') ...[
            const SizedBox(height: 6),
            Text(d!['disclaimer'], style: const TextStyle(fontSize: 11, color: Colors.black45)),
          ],
          if (!m.mine) _controls(m),
        ]),
      ),
    );
  }
}
