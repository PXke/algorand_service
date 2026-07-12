import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/api/api_errors.dart';
import '../../../core/l10n/l10n_extensions.dart';
import '../../../core/providers/api_providers.dart';
import '../../../core/theme/app_theme_extension.dart';
import '../../../core/ui/error_banner.dart';
import '../../../core/ui/layout.dart';
import '../../../core/ui/page_content.dart';
import '../../../core/ui/page_header.dart';

/// Public contact form — the only inbound channel to the newsroom. Messages
/// land in the admin Inbox tab; there is no e-mail address to expose.
class ContactPage extends ConsumerStatefulWidget {
  const ContactPage({super.key});

  @override
  ConsumerState<ContactPage> createState() => _ContactPageState();
}

class _ContactPageState extends ConsumerState<ContactPage> {
  final _nameController = TextEditingController();
  final _emailController = TextEditingController();
  final _messageController = TextEditingController();
  bool _sending = false;
  bool _sent = false;
  String? _error;

  @override
  void dispose() {
    _nameController.dispose();
    _emailController.dispose();
    _messageController.dispose();
    super.dispose();
  }

  Future<void> _send() async {
    final l10n = context.l10n;
    final message = _messageController.text.trim();
    if (message.length < 10) {
      setState(() => _error = l10n.contactTooShort);
      return;
    }
    setState(() {
      _sending = true;
      _error = null;
    });
    try {
      await ref.read(apiClientProvider).postJson(
        '/api/v1/contact',
        body: {
          'message': message,
          'name': _nameController.text.trim(),
          'email': _emailController.text.trim(),
        },
      );
      if (!mounted) return;
      setState(() {
        _sent = true;
        _sending = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = apiErrorMessage(e);
        _sending = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final colors = context.appColors;

    // Form fields sit directly on the page (the standard editorial layout);
    // panels are reserved for placements, not pages.
    return PageScroll(
      children: [
        PageHeader(
          title: l10n.contactTitle,
          subtitle: l10n.contactSubtitle,
        ),
        ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: AppLayout.maxReadingWidth),
          child: Padding(
            padding: EdgeInsets.zero,
            child: _sent
                ? _SentConfirmation(message: l10n.contactSent)
                : Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      TextField(
                        controller: _nameController,
                        textInputAction: TextInputAction.next,
                        decoration: InputDecoration(
                          labelText: l10n.contactNameLabel,
                          filled: true,
                          fillColor: colors.panelBackground,
                        ),
                      ),
                      const SizedBox(height: 12),
                      TextField(
                        controller: _emailController,
                        textInputAction: TextInputAction.next,
                        keyboardType: TextInputType.emailAddress,
                        decoration: InputDecoration(
                          labelText: l10n.contactEmailLabel,
                          filled: true,
                          fillColor: colors.panelBackground,
                        ),
                      ),
                      const SizedBox(height: 12),
                      TextField(
                        controller: _messageController,
                        maxLines: 8,
                        maxLength: 4000,
                        decoration: InputDecoration(
                          labelText: l10n.contactMessageLabel,
                          hintText: l10n.contactMessageHint,
                          alignLabelWithHint: true,
                          filled: true,
                          fillColor: colors.panelBackground,
                        ),
                      ),
                      const SizedBox(height: 16),
                      Align(
                        alignment: Alignment.centerRight,
                        child: FilledButton.icon(
                          onPressed: _sending ? null : _send,
                          icon: _sending
                              ? const SizedBox(
                                  width: 16,
                                  height: 16,
                                  child: CircularProgressIndicator(strokeWidth: 2),
                                )
                              : const Icon(Icons.send_outlined, size: 18),
                          label: Text(l10n.contactSend),
                        ),
                      ),
                    ],
                  ),
          ),
        ),
        const SizedBox(height: AppLayout.itemGap),
        if (_error != null) ErrorBanner(message: _error!),
      ],
    );
  }
}

class _SentConfirmation extends StatelessWidget {
  const _SentConfirmation({required this.message});

  final String message;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = context.appColors;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 24),
      child: Column(
        children: [
          Icon(Icons.mark_email_read_outlined, size: 44, color: colors.accent),
          const SizedBox(height: 14),
          Text(
            message,
            textAlign: TextAlign.center,
            style: theme.textTheme.bodyLarge,
          ),
        ],
      ),
    );
  }
}
