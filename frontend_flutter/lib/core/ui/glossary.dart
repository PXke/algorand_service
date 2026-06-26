import 'package:flutter/material.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:markdown/markdown.dart' as md;

/// Curated glossary of Algorand / DeFi jargon. Keys are the display form;
/// matching is case-insensitive but the list is kept to *distinctive* terms
/// (mostly acronyms and protocol-specific words) so everyday words are never
/// tooltipped. Definitions are one short sentence — they render inside a
/// hover/long-press [Tooltip], not a panel.
const Map<String, String> kGlossary = {
  'AMM':
      'Automated Market Maker — a DEX design where prices come from a formula over pooled liquidity instead of an order book.',
  'TVL':
      'Total Value Locked — the total worth of assets deposited in a protocol, a common gauge of its size.',
  'APY':
      'Annual Percentage Yield — yearly return on a deposit including the effect of compounding.',
  'APR':
      'Annual Percentage Rate — yearly return on a deposit without compounding.',
  'DeFi':
      'Decentralized Finance — financial services (lending, trading, yield) run by smart contracts rather than intermediaries.',
  'DEX':
      'Decentralized Exchange — a venue where users trade tokens peer-to-peer via smart contracts.',
  'ASA':
      'Algorand Standard Asset — Algorand’s native token standard for fungible and non-fungible assets.',
  'ASC1':
      'Algorand Smart Contract (Layer-1) — on-chain logic running in the Algorand Virtual Machine.',
  'AVM':
      'Algorand Virtual Machine — the runtime that executes smart contracts on Algorand.',
  'TEAL':
      'Transaction Execution Approval Language — the low-level assembly that Algorand smart contracts compile to.',
  'AlgoKit':
      'Algorand’s official developer toolkit for building, testing and deploying applications.',
  'ARC':
      'Algorand Request for Comments — a community standard proposal (e.g. ARC-3, ARC-19 for NFTs).',
  'ABI':
      'Application Binary Interface — the agreed way to call a smart contract’s methods.',
  'PPoS':
      'Pure Proof-of-Stake — Algorand’s consensus where influence is proportional to stake and every holder can participate.',
  'MBR':
      'Minimum Balance Requirement — the small amount of ALGO an account must hold to cover its assets and state.',
  'LST':
      'Liquid Staking Token — a tradable token representing staked assets that keeps earning while staying usable.',
  'dApp':
      'Decentralized Application — an app whose backend logic runs on a blockchain.',
  'DAO':
      'Decentralized Autonomous Organization — a member-governed entity whose rules are enforced by smart contracts.',
  'NFT':
      'Non-Fungible Token — a unique, indivisible on-chain asset, often representing art or collectibles.',
  'RWA':
      'Real-World Asset — an off-chain asset (bonds, real estate, commodities) represented as a token on-chain.',
  'LP':
      'Liquidity Provider — someone who deposits tokens into a pool to enable trading and earn fees.',
  'state proof':
      'A succinct cryptographic certificate that lets other chains verify Algorand’s state without a full node.',
  'atomic transfer':
      'A group of Algorand transactions that all succeed or all fail together, with no intermediary needed.',
  'rekey':
      'An Algorand feature that hands signing authority for an account to a different key without changing its address.',
  'clawback':
      'An ASA permission letting a designated address revoke or move tokens from any holder.',
  'governance':
      'Algorand’s on-chain program where ALGO holders commit stake and vote on ecosystem decisions for rewards.',
};

String? lookupGlossaryDefinition(String term) {
  final hit = kGlossary[term];
  if (hit != null) return hit;
  // Case-insensitive fall-through (e.g. "amm" / "Amm").
  final lower = term.toLowerCase();
  for (final entry in kGlossary.entries) {
    if (entry.key.toLowerCase() == lower) return entry.value;
  }
  return null;
}

/// Regex alternation of the glossary terms, longest first so multi-word terms
/// ("state proof") win over any substring. Acronyms are bounded by \b; this is
/// compiled once.
final RegExp _glossaryPattern = _buildGlossaryPattern();

RegExp _buildGlossaryPattern() {
  final terms = kGlossary.keys.toList()
    ..sort((a, b) => b.length.compareTo(a.length));
  final escaped = terms.map((t) => RegExp.escape(t)).join('|');
  return RegExp('\\b(?:$escaped)\\b');
}

/// Inline markdown syntax that turns a glossary term into a `<glossary>` element.
/// Only the FIRST occurrence per render is marked (tracked in [_seen]) to avoid
/// peppering the article with underlines. A fresh instance is created per build.
class GlossaryInlineSyntax extends md.InlineSyntax {
  GlossaryInlineSyntax() : super(_glossaryPattern.pattern, caseSensitive: false);

  final Set<String> _seen = <String>{};

  @override
  bool onMatch(md.InlineParser parser, Match match) {
    final text = match[0]!;
    final key = text.toLowerCase();
    if (_seen.contains(key) || lookupGlossaryDefinition(text) == null) {
      // Not a glossary term we want to mark (or already marked once): emit as
      // plain text and let the parser continue past it.
      parser.addNode(md.Text(text));
      return true;
    }
    _seen.add(key);
    parser.addNode(md.Element.text('glossary', text));
    return true;
  }
}

/// Renders a `<glossary>` element as an inline dotted-underline term with a
/// hover (web/desktop) / long-press (touch) [Tooltip] carrying the definition.
class GlossaryElementBuilder extends MarkdownElementBuilder {
  GlossaryElementBuilder({required this.accent});

  final Color accent;

  @override
  Widget? visitElementAfterWithContext(
    BuildContext context,
    md.Element element,
    TextStyle? preferredStyle,
    TextStyle? parentStyle,
  ) {
    final term = element.textContent;
    final definition = lookupGlossaryDefinition(term);
    if (definition == null) return null;
    final base = parentStyle ?? preferredStyle ?? DefaultTextStyle.of(context).style;
    // Return the term as a WidgetSpan wrapped in a Text.rich — NOT a bare Tooltip
    // widget. flutter_markdown's _mergeInlineChildren only merges Text/RichText
    // widgets into the paragraph's single RichText; a bare widget becomes its own
    // Wrap child, which pushed the following prose onto a new line around every
    // term. Baseline alignment keeps the term sitting on the text baseline.
    return Text.rich(
      WidgetSpan(
        alignment: PlaceholderAlignment.baseline,
        baseline: TextBaseline.alphabetic,
        child: Tooltip(
          message: definition,
          preferBelow: false,
          waitDuration: const Duration(milliseconds: 300),
          child: Text(
            term,
            style: base.copyWith(
              decoration: TextDecoration.underline,
              decorationStyle: TextDecorationStyle.dotted,
              decorationColor: accent.withValues(alpha: 0.8),
            ),
          ),
        ),
      ),
    );
  }
}
