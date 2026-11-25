import 'package:flutter/material.dart';

import '../services/api_service.dart';
import '../theme.dart';
import '../widgets/common.dart';

/// Patient: invite caregivers, choose exactly what each may do, revoke at any time.
class CaregiversScreen extends StatefulWidget {
  const CaregiversScreen({super.key});
  @override
  State<CaregiversScreen> createState() => _CaregiversScreenState();
}

class _CaregiversScreenState extends State<CaregiversScreen> {
  late Future<List> _links;
  static const perms = {
    'can_request_emergency': ('Ask for emergency help for me', Icons.emergency),
    'can_request_tests': ('Request medical tests for me', Icons.biotech),
    'can_respond_checkins': ('Answer check-ins / report symptoms', Icons.forum),
    'can_view_vitals': ('See my vital signs', Icons.monitor_heart),
    'receives_notifications': ('Get my alerts', Icons.notifications),
  };

  @override
  void initState() {
    super.initState();
    _reload();
  }

  void _reload() => setState(() {
        _links = ApiService.instance.get('/caregivers/').then((v) => v as List);
      });

  Future<void> _invite() async {
    final rel = TextEditingController();
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Invite a caregiver'),
        content: TextField(controller: rel, decoration: const InputDecoration(labelText: 'Who is it? (e.g. daughter)')),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('Create code')),
        ],
      ),
    );
    if (ok != true) return;
    try {
      final l = Map<String, dynamic>.from(await ApiService.instance.post('/caregivers/invite/', {'relationship': rel.text}));
      if (!mounted) return;
      await showDialog(
        context: context,
        builder: (c) => AlertDialog(
          title: const Text('Give this code to your caregiver'),
          content: Column(mainAxisSize: MainAxisSize.min, children: [
            SelectableText(l['invite_code'] ?? '', style: const TextStyle(fontSize: 34, fontWeight: FontWeight.w900, letterSpacing: 4)),
            const SizedBox(height: 8),
            const Text('They create a caregiver account in the app and enter this code. It works once.'),
          ]),
          actions: [FilledButton(onPressed: () => Navigator.pop(c), child: const Text('Done'))],
        ),
      );
      _reload();
    } catch (e) {
      if (mounted) toast(context, e, error: true);
    }
  }

  Future<void> _toggle(Map l, String perm, bool v) async {
    try {
      await ApiService.instance.patch('/caregivers/${l['id']}/', {perm: v});
      _reload();
    } catch (e) {
      if (mounted) toast(context, e, error: true);
    }
  }

  Future<void> _revoke(Map l) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: Text('Remove ${l['name'] ?? 'this invitation'}?'),
        content: const Text('They will no longer be able to see or do anything for you.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Keep')),
          FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('Remove')),
        ],
      ),
    );
    if (ok != true) return;
    await ApiService.instance.delete('/caregivers/${l['id']}/');
    _reload();
  }

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(title: const Text('My caregivers')),
        floatingActionButton: FloatingActionButton.extended(onPressed: _invite, icon: const Icon(Icons.person_add), label: const Text('Invite')),
        body: FutureBuilder<List>(
          future: _links,
          builder: (c, s) {
            if (s.hasError) return ErrorRetry(s.error!, _reload);
            if (!s.hasData) return const Center(child: CircularProgressIndicator());
            if (s.data!.isEmpty) {
              return const EmptyState(Icons.family_restroom, 'No caregivers yet. A caregiver can help you ask for help, '
                  'answer check-ins and receive your alerts - only with the permissions you choose.');
            }
            return ListView(padding: const EdgeInsets.fromLTRB(16, 8, 16, 90), children: [
              for (final l in s.data!.map((e) => Map<String, dynamic>.from(e)))
                Padding(
                  padding: const EdgeInsets.only(bottom: 12),
                  child: Card(
                    child: Padding(
                      padding: const EdgeInsets.all(12),
                      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                        Row(children: [
                          const Icon(Icons.person, color: AppColors.primary),
                          const SizedBox(width: 8),
                          Expanded(
                            child: Text(l['name'] ?? 'Invitation - code ${l['invite_code']}',
                                style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 16)),
                          ),
                          if ((l['relationship'] ?? '').toString().isNotEmpty) Chip(label: Text(l['relationship'])),
                          IconButton(tooltip: 'Remove', onPressed: () => _revoke(l), icon: const Icon(Icons.delete_outline)),
                        ]),
                        for (final p in perms.entries)
                          SwitchListTile(
                            dense: true,
                            contentPadding: EdgeInsets.zero,
                            secondary: Icon(p.value.$2),
                            title: Text(p.value.$1),
                            value: l[p.key] == true,
                            onChanged: (v) => _toggle(l, p.key, v),
                          ),
                      ]),
                    ),
                  ),
                ),
            ]);
          },
        ),
      );
}
