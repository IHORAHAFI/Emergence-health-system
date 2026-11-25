import 'dart:async';

import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../services/api_service.dart';
import '../theme.dart';
import '../widgets/common.dart';
import 'assistant_screen.dart';

/// Searchable, medically reviewed disease library.
class DiseasesScreen extends StatefulWidget {
  const DiseasesScreen({super.key});
  @override
  State<DiseasesScreen> createState() => _DiseasesScreenState();
}

class _DiseasesScreenState extends State<DiseasesScreen> {
  final _q = TextEditingController();
  Timer? _debounce;
  late Future<List<Map<String, dynamic>>> _items;

  @override
  void initState() {
    super.initState();
    _search();
  }

  void _search() => setState(() {
        _items = ApiService.instance.diseases(q: _q.text.trim());
      });

  @override
  void dispose() {
    _debounce?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Diseases')),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const AssistantScreen())),
        icon: const Icon(Icons.smart_toy_outlined),
        label: const Text('Ask AI'),
      ),
      body: Column(children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 4, 16, 8),
          child: TextField(
            controller: _q,
            textInputAction: TextInputAction.search,
            decoration: const InputDecoration(prefixIcon: Icon(Icons.search), hintText: 'Disease or symptom'),
            onChanged: (_) {
              _debounce?.cancel();
              _debounce = Timer(const Duration(milliseconds: 400), _search);
            },
            onSubmitted: (_) => _search(),
          ),
        ),
        Expanded(
          child: FutureBuilder<List<Map<String, dynamic>>>(
            future: _items,
            builder: (c, s) {
              if (s.hasError) return ErrorRetry(s.error!, _search);
              if (!s.hasData) return const Center(child: CircularProgressIndicator());
              if (s.data!.isEmpty) return const EmptyState(Icons.search_off, 'No results');
              return ListView.separated(
                padding: const EdgeInsets.fromLTRB(16, 4, 16, 90),
                itemCount: s.data!.length,
                separatorBuilder: (_, __) => const SizedBox(height: 10),
                itemBuilder: (c, i) {
                  final d = s.data![i];
                  return Card(
                    child: ListTile(
                      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
                      title: Text(d['name'], style: const TextStyle(fontWeight: FontWeight.w700)),
                      subtitle: Text(d['category_display'] ?? ''),
                      trailing: d['reviewed'] == true
                          ? const Icon(Icons.verified, color: AppColors.ok)
                          : const Icon(Icons.chevron_right),
                      onTap: () => Navigator.push(
                          context, MaterialPageRoute(builder: (_) => DiseaseDetailScreen(slug: d['slug']))),
                    ),
                  );
                },
              );
            },
          ),
        ),
      ]),
    );
  }
}

class DiseaseDetailScreen extends StatelessWidget {
  final String slug;
  const DiseaseDetailScreen({super.key, required this.slug});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(),
      body: FutureBuilder<Map<String, dynamic>>(
        future: ApiService.instance.disease(slug),
        builder: (c, s) {
          if (s.hasError) return EmptyState(Icons.cloud_off, '${s.error}');
          if (!s.hasData) return const Center(child: CircularProgressIndicator());
          final d = s.data!;
          List<String> l(String k) => List<String>.from(d[k] ?? const []);
          return ListView(padding: const EdgeInsets.fromLTRB(16, 0, 16, 32), children: [
            Text(d['name'], style: const TextStyle(fontSize: 26, fontWeight: FontWeight.w800)),
            const SizedBox(height: 6),
            Row(children: [
              Text(d['category_display'] ?? '', style: const TextStyle(color: Colors.black54)),
              const SizedBox(width: 8),
              if (d['reviewed'] == true)
                const Chip(
                    avatar: Icon(Icons.verified, size: 16, color: AppColors.ok),
                    label: Text('Medically reviewed'),
                    visualDensity: VisualDensity.compact),
            ]),
            const SizedBox(height: 8),
            Text(d['description'] ?? '', style: const TextStyle(fontSize: 15, height: 1.4)),
            if (l('warning_signs').isNotEmpty)
              _box('Get help now if', l('warning_signs'), Icons.warning_amber, AppColors.emergency),
            _section('Signs & symptoms', l('symptoms'), Icons.sick_outlined),
            if ((d['treatment'] ?? '').toString().isNotEmpty) _text('Treatment', d['treatment'], Icons.healing),
            _section('Prevention', l('prevention'), Icons.shield_outlined),
            _section('Risk factors', l('risk_factors'), Icons.groups_outlined),
            const SizedBox(height: 16),
            if ((d['source_url'] ?? '').toString().isNotEmpty)
              OutlinedButton.icon(
                onPressed: () => launchUrl(Uri.parse(d['source_url']), mode: LaunchMode.externalApplication),
                icon: const Icon(Icons.open_in_new),
                label: Text('Source: ${d['source_name']}'),
              ),
            const SizedBox(height: 8),
            const Text('General information only - not a diagnosis. Ask a health professional about your case.',
                textAlign: TextAlign.center, style: TextStyle(color: Colors.black45, fontSize: 12)),
          ]);
        },
      ),
    );
  }

  Widget _section(String title, List<String> items, IconData icon) => items.isEmpty
      ? const SizedBox.shrink()
      : Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          SectionTitle(title),
          for (final i in items)
            Padding(
              padding: const EdgeInsets.only(bottom: 6),
              child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Icon(icon, size: 18, color: AppColors.primary),
                const SizedBox(width: 10),
                Expanded(child: Text(i, style: const TextStyle(fontSize: 15))),
              ]),
            ),
        ]);

  Widget _text(String title, String body, IconData icon) => Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        SectionTitle(title),
        Card(child: Padding(padding: const EdgeInsets.all(14), child: Text(body, style: const TextStyle(fontSize: 15, height: 1.4)))),
      ]);

  Widget _box(String title, List<String> items, IconData icon, Color color) => Container(
        margin: const EdgeInsets.only(top: 16),
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(color: color.withValues(alpha: .08), borderRadius: BorderRadius.circular(16)),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Icon(icon, color: color),
            const SizedBox(width: 8),
            Text(title, style: TextStyle(color: color, fontWeight: FontWeight.w800)),
          ]),
          const SizedBox(height: 8),
          for (final i in items) Text('• $i', style: const TextStyle(fontSize: 15)),
        ]),
      );
}
