// ignore: unused_import
import 'package:intl/intl.dart' as intl;
import 'app_localizations.dart';

// ignore_for_file: type=lint

/// The translations for English (`en`).
class AppLocalizationsEn extends AppLocalizations {
  AppLocalizationsEn([String locale = 'en']) : super(locale);

  @override
  String get appTitle => 'PXke Algorand';

  @override
  String get appTagline => 'Independent coverage of the Algorand ecosystem';

  @override
  String get navHome => 'Home';

  @override
  String get navNews => 'News';

  @override
  String get navSources => 'Sources';

  @override
  String get navSuggestions => 'Suggestions';

  @override
  String get navSearch => 'Search';

  @override
  String get navAdmin => 'Admin';

  @override
  String get navProducts => 'PRODUCTS';

  @override
  String get navWallet => 'Wallet';

  @override
  String get navAppearance => 'APPEARANCE';

  @override
  String get pageTitleHome => 'PXke Algorand Projects';

  @override
  String get pageTitleArticle => 'Article';

  @override
  String get pageTitleNews => 'News';

  @override
  String get pageTitleSources => 'News sources';

  @override
  String get pageTitleSuggestions => 'Suggestions';

  @override
  String get pageTitleSearch => 'Search';

  @override
  String get pageTitleAdmin => 'Admin';

  @override
  String get backToFeed => 'Back to feed';

  @override
  String get homeWelcome => 'PXke Algorand ecosystem platform';

  @override
  String get homeTagline =>
      'Curated news from on-chain triggers and crawled sources, community suggestions, and search.';

  @override
  String get homeNewsDescription =>
      'Curated feed of articles published when monitored sources or chain events change.';

  @override
  String get homeSourcesDescription =>
      'Registered Discord, Reddit, and web crawlers that power the news pipeline.';

  @override
  String get homeSuggestionsDescription =>
      'Submit and upvote community ideas with wallet-backed authentication.';

  @override
  String get homeSearchDescription =>
      'Full-text search across published articles.';

  @override
  String get homeOpenProduct => 'Open';

  @override
  String get themeLight => 'Light';

  @override
  String get themeDark => 'Dark';

  @override
  String get themeSystem => 'System';

  @override
  String get themeSwitchToLight => 'Switch to light theme';

  @override
  String get themeSwitchToDark => 'Switch to dark theme';

  @override
  String get localeSystem => 'System language';

  @override
  String get localeEnglish => 'English';

  @override
  String get localeSpanish => 'Español';

  @override
  String get localeFrench => 'Français';

  @override
  String get localeArabic => 'العربية';

  @override
  String get localeChinese => '中文';

  @override
  String get localeHindi => 'हिन्दी';

  @override
  String get localeRussian => 'Русский';

  @override
  String get localeDari => 'دری';

  @override
  String get localePashto => 'پښتو';

  @override
  String get navLanguage => 'LANGUAGE';

  @override
  String get walletConnected => 'Connected';

  @override
  String get walletDisconnect => 'Disconnect';

  @override
  String get walletSignInTitle => 'Sign in with wallet';

  @override
  String get walletSignInBody =>
      'Connect a WalletConnect-compatible Algorand wallet to submit suggestions and upvotes.';

  @override
  String get walletConnect => 'Connect wallet';

  @override
  String get walletConnectFailed =>
      'Connection failed. Try again or cancel the pairing dialog first.';

  @override
  String get walletDialogTitle => 'Connect your wallet';

  @override
  String get walletDialogBody =>
      'Scan the QR code with a WalletConnect-compatible Algorand wallet (Pera, Defly, etc.) or copy/open the link on mobile.';

  @override
  String get walletAwaitingApproval =>
      'Wallet connected. Switch back to your wallet app to approve the sign-in request, then return here.';

  @override
  String get walletAwaitingApprovalTitle => 'Almost there';

  @override
  String get walletCopyUri => 'Copy URI';

  @override
  String get walletUriCopied => 'WalletConnect URI copied';

  @override
  String get walletOpenWallet => 'Open wallet';

  @override
  String get walletOpenFailed =>
      'Could not open wallet app — copy the URI and paste it in Pera or Defly';

  @override
  String get walletCancel => 'Cancel';

  @override
  String get walletDone => 'Done';

  @override
  String get walletErrorTitle => 'Sign-in failed';

  @override
  String get walletErrorTimeout =>
      'The sign-in request timed out. Open your wallet app and try again.';

  @override
  String get walletErrorRejected => 'The request was declined in the wallet.';

  @override
  String get walletErrorGeneric =>
      'Could not complete the sign-in. Check your connection and try again.';

  @override
  String get walletRetry => 'Try again';

  @override
  String get walletShowQr => 'Show QR code';

  @override
  String get walletMobileHint =>
      'Tap the button below to open your Algorand wallet, approve the connection, then return here.';

  @override
  String get walletSignExplainer =>
      'Your wallet will ask you to sign a sign-in message (older wallets show a 0-ALGO transaction instead). Signing is free and nothing is sent to the network — it only proves you own the address.';

  @override
  String get newsFeedTitle => 'Latest articles';

  @override
  String get newsSubtitleDefault =>
      'On-chain triggers and crawled sources publish here when content changes.';

  @override
  String get articlePublicationDetailsHint =>
      'Publisher, date, and source link';

  @override
  String get newsPriceUnavailable =>
      'ALGO price unavailable — run price metrics collection';

  @override
  String newsArticleCount(int count) {
    return '$count articles in the curated feed';
  }

  @override
  String get newsEmptyFilteredTitle => 'No articles yet';

  @override
  String get newsEmptyTitle => 'Feed is empty';

  @override
  String get newsSponsoredLabel => 'Sponsored';

  @override
  String newsSponsoredBy(String sponsor) {
    return 'Sponsored · $sponsor';
  }

  @override
  String get newsEmptyFilteredMessage =>
      'This source has not published any articles. Check that workers are polling and content has changed.';

  @override
  String get newsEmptyMessage =>
      'Start Conduit, Celery workers, and seed service_registry to populate the feed.';

  @override
  String newsFilterShowing(String serviceId) {
    return 'Showing articles from $serviceId';
  }

  @override
  String get clearFilter => 'Clear';

  @override
  String get articleUntitled => 'Untitled';

  @override
  String get articleSectionTitle => 'Article';

  @override
  String get articlePublicationDetails => 'Publication details';

  @override
  String get articleMetaService => 'Service';

  @override
  String get articleMetaPublisher => 'Publisher';

  @override
  String get articleMetaDataSource => 'Data source';

  @override
  String get articleMetaCoinGecko => 'CoinGecko market data';

  @override
  String get articleMetaPublished => 'Published';

  @override
  String get articleMetaRound => 'Round';

  @override
  String get articleMetaTriggerTx => 'Trigger tx';

  @override
  String get articleMetaSourceUrl => 'Source URL';

  @override
  String get articleOpenInBrowser => 'Open in browser';

  @override
  String get articleViewOnExplorer => 'View on Algorand explorer';

  @override
  String articleMoreFrom(String serviceId) {
    return 'More from $serviceId';
  }

  @override
  String get articleViewSource => 'View source';

  @override
  String get adminTitle => 'Source administration';

  @override
  String get adminSubtitle =>
      'Registered crawlers and feeds (visible to admin wallets only).';

  @override
  String get adminViewFeedArticles => 'View feed for this source';

  @override
  String get adminAccessDenied =>
      'Connect an admin wallet to manage sources. Set ADMIN_WALLET_ADDRESSES at build time.';

  @override
  String get sourcesTitle => 'News sources';

  @override
  String get sourcesSubtitle =>
      'Registered services crawled by workers. Discord and Reddit are polled on a schedule; on-chain matches trigger publishes when monitored content changes.';

  @override
  String get sourcesEmptyTitle => 'No sources configured';

  @override
  String get sourcesEmptyMessage =>
      'Seed service_registry with Discord and Reddit TOML files, then run migrations. Sources appear here once registered in Cassandra.';

  @override
  String get sourcesMetaServiceId => 'Service ID';

  @override
  String get sourcesMetaScrapeUrl => 'Scrape URL';

  @override
  String get sourcesMetaMatchRule => 'Match rule';

  @override
  String get sourcesViewArticles => 'View articles';

  @override
  String get sourcesDisabled => 'Disabled';

  @override
  String filterAll(int count) {
    return 'All ($count)';
  }

  @override
  String filterDiscord(int count) {
    return 'Discord ($count)';
  }

  @override
  String filterReddit(int count) {
    return 'Reddit ($count)';
  }

  @override
  String filterWeb(int count) {
    return 'Web ($count)';
  }

  @override
  String matchRuleValue(String kind, String value) {
    return '$kind = $value';
  }

  @override
  String get sourceKindDiscord => 'Discord';

  @override
  String get sourceKindReddit => 'Reddit';

  @override
  String get sourceKindWeb => 'Web';

  @override
  String get sourceKindOnChain => 'On-chain';

  @override
  String get sourceKindUnknown => 'Source';

  @override
  String get navContact => 'Contact';

  @override
  String get contactTitle => 'Contact';

  @override
  String get contactSubtitle =>
      'Corrections, tips or feedback — write to the newsroom.';

  @override
  String get contactNameLabel => 'Your name (optional)';

  @override
  String get contactEmailLabel => 'Email (optional, if you want a reply)';

  @override
  String get contactMessageLabel => 'Message';

  @override
  String get contactMessageHint => 'Corrections, tips, feedback…';

  @override
  String get contactSend => 'Send message';

  @override
  String get contactSent => 'Message sent — thank you for writing to us.';

  @override
  String get contactTooShort =>
      'Please write a few more words (at least 10 characters).';

  @override
  String get searchTitle => 'Search';

  @override
  String get searchSubtitle => 'Search every article we have published.';

  @override
  String get searchQueryLabel => 'Search query';

  @override
  String get searchQueryHint => 'Keywords, titles, summaries…';

  @override
  String get searchAction => 'Search';

  @override
  String searchEngine(String name) {
    return 'Engine: $name';
  }

  @override
  String get searchEmptyTitle => 'No results';

  @override
  String get searchEmptyMessage =>
      'Try different keywords or check that articles are indexed.';

  @override
  String get searchErrorBackend =>
      'Search is temporarily unavailable. Check that the API and database are running.';

  @override
  String get suggestionsTitle => 'Suggestions';

  @override
  String get suggestionsSubtitle =>
      'Submit after sending at least 0.01 ALGO to the platform treasury. Upvotes use an off-chain wallet signature.';

  @override
  String get suggestionsNewTitle => 'New suggestion';

  @override
  String get suggestionsFieldTitle => 'Title';

  @override
  String get suggestionsFieldBody => 'Body';

  @override
  String get suggestionsFieldTxid => 'Submission transaction ID';

  @override
  String get suggestionsSubmit => 'Submit suggestion';

  @override
  String get suggestionsUpvoteTitle => 'Upvote';

  @override
  String get suggestionsSignatureLabel => 'Signature (base64)';

  @override
  String get suggestionsSignatureHint =>
      'After signing the prepared message in your wallet';

  @override
  String get suggestionsSubmitUpvote => 'Submit upvote';

  @override
  String get suggestionsPrepareUpvote => 'Prepare upvote';

  @override
  String get suggestionsUpvoteDialogTitle => 'Sign upvote message';

  @override
  String get suggestionsCopyMessage => 'Copy message';

  @override
  String get suggestionsMessageCopied => 'Signing message copied to clipboard';

  @override
  String suggestionsTreasuryHelp(String minAlgo, String address) {
    return 'Send at least $minAlgo ALGO to the platform treasury before submitting:\n$address';
  }

  @override
  String suggestionsUpvoteCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count upvotes',
      one: '1 upvote',
      zero: 'No upvotes yet',
    );
    return '$_temp0';
  }

  @override
  String get close => 'Close';

  @override
  String suggestionsTxShort(String txid) {
    return 'Tx $txid';
  }

  @override
  String get snackConnectWallet => 'Connect your wallet first';

  @override
  String get snackSuggestionSubmitted => 'Suggestion submitted';

  @override
  String get snackUpvoteRecorded => 'Upvote recorded';

  @override
  String get snackChooseSuggestionUpvote =>
      'Choose a suggestion to upvote first';

  @override
  String metaPublishedEpoch(String epoch) {
    return 'Published: $epoch';
  }

  @override
  String metaPublishedRelative(String when) {
    return 'Published: $when';
  }

  @override
  String get timeJustNow => 'just now';

  @override
  String timeMinutesAgo(int count) {
    return '${count}m ago';
  }

  @override
  String timeHoursAgo(int count) {
    return '${count}h ago';
  }

  @override
  String timeDaysAgo(int count) {
    return '${count}d ago';
  }

  @override
  String metaRound(String round) {
    return 'Round: $round';
  }

  @override
  String metaService(String serviceId) {
    return 'Service: $serviceId';
  }

  @override
  String get navFrontPage => 'Front page';

  @override
  String get navLatest => 'Latest';

  @override
  String get navSections => 'SECTIONS';

  @override
  String get navAbout => 'About';

  @override
  String get navApps => 'Apps';

  @override
  String get navProductsMenuHint => 'Explore the platform';

  @override
  String get frontPageTopStories => 'Top stories';

  @override
  String get frontPageLatest => 'Latest';

  @override
  String get frontPageMore => 'More from the newsroom';

  @override
  String frontPageSectionStories(String section) {
    return 'More in $section';
  }

  @override
  String get navHot => 'Hot';

  @override
  String get navTopics => 'Topics';

  @override
  String get hotTitle => 'Most read';

  @override
  String get hotLead => 'The stories readers are opening most right now.';

  @override
  String get topicsTitle => 'Topics';

  @override
  String get topicsLead =>
      'Every tag the newsroom used recently — sized by coverage, warmed by reads.';

  @override
  String topicSubtitle(String tag) {
    return 'Stories tagged “$tag”';
  }

  @override
  String readsCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count reads',
      one: '1 read',
    );
    return '$_temp0';
  }

  @override
  String get sectionMarkets => 'Markets';

  @override
  String get sectionSecurity => 'Security';

  @override
  String get sectionDevelopers => 'Developers';

  @override
  String get sectionCommunity => 'Community';

  @override
  String get sectionEcosystem => 'Ecosystem';

  @override
  String get sectionEmptyTitle => 'Nothing here yet';

  @override
  String get sectionEmptyMessage =>
      'No stories have been filed in this section. Check back soon.';

  @override
  String get bylineNewsroom => 'The Newsroom';

  @override
  String get bylineMarketsDesk => 'Markets Desk';

  @override
  String get bylineChainDesk => 'On-Chain Desk';

  @override
  String articleByline(String desk) {
    return 'By $desk';
  }

  @override
  String articleReadingTime(int count) {
    return '$count min read';
  }

  @override
  String get articleRelatedTitle => 'Related stories';

  @override
  String get articleShare => 'Share';

  @override
  String get articleLinkCopied => 'Link copied to clipboard';

  @override
  String get footerTagline =>
      'Independent, automated coverage of the Algorand ecosystem.';

  @override
  String get footerSectionsHeading => 'Sections';

  @override
  String get footerAboutHeading => 'About';

  @override
  String footerRights(String year) {
    return '© $year PXke Algorand. Independent coverage of the Algorand ecosystem.';
  }

  @override
  String get aboutTitle => 'About PXke Algorand';

  @override
  String get aboutLead =>
      'PXke Algorand is an independent newsroom covering the Algorand blockchain — its markets, protocol, governance, and the projects building on it.';

  @override
  String get aboutHowHeading => 'How we publish';

  @override
  String get aboutHowBody =>
      'Our coverage is assembled automatically from on-chain events, scheduled market data, and monitored community sources, then organised into sections by our editorial pipeline. Every story carries a provenance line so you can trace where it came from.';

  @override
  String get aboutAiHeading => 'Written with AI';

  @override
  String get aboutAiBody =>
      'PXke Algorand publishes AI-assisted journalism. Our articles are drafted by AI language models from the on-chain events, market data, and community sources above, under automated editorial review — they are machine-generated rather than written by human reporters. We link to the original sources on every story so you can verify each claim, and the organisation, not an individual byline, is the author of record.';

  @override
  String get aboutProvenanceHeading => 'Provenance & transparency';

  @override
  String get aboutProvenanceBody =>
      'On-chain stories are triggered by verifiable transactions and link back to the Algorand explorer. Market stories draw on CoinGecko data. Sponsored placements are always labelled as such and are kept clearly separate from editorial.';

  @override
  String get aboutStandardsHeading => 'Editorial standards';

  @override
  String get aboutStandardsBody =>
      'We aim for accuracy and clear attribution. This publication is not investment advice. Spotted an error? Reach out through the source links on any story.';

  @override
  String get seedsTitle => 'Seeds';

  @override
  String get seedsSubtitle =>
      'Manually-configured starting points for the crawler. Domains approved from discovery live in the Domains tab.';

  @override
  String get sourcesAdd => 'Add source';

  @override
  String get sourcesEdit => 'Edit';

  @override
  String get sourcesEditTitle => 'Edit source';

  @override
  String get sourcesAddTitle => 'Add source';

  @override
  String get sourcesDeleteTitle => 'Delete source?';

  @override
  String sourcesDeleteBody(String serviceId) {
    return '\"$serviceId\" will stop being crawled. Articles already published stay.';
  }

  @override
  String get sourcesDelete => 'Delete';

  @override
  String get sourcesSave => 'Save';

  @override
  String get sourcesAddAction => 'Add';

  @override
  String get sourcesRequiredFields => 'Service id, name and URL are required.';

  @override
  String get sourcesChangesNextPoll =>
      'Changes apply on the next crawler poll.';

  @override
  String get sourcesMerge => 'Merge';

  @override
  String get sourcesMergeTitle => 'Merge services';

  @override
  String get sourcesMergeIntro =>
      'Fold several services into one. The chosen services\' sources and domains move to the target; the emptied services are disabled. Use this when one product spans multiple domains (e.g. algorand.co + algorand.com).';

  @override
  String get sourcesMergeTarget => 'Keep as target';

  @override
  String get sourcesMergeFold => 'Fold in (will be disabled)';

  @override
  String get sourcesMergeAction => 'Merge';

  @override
  String get sourcesMergeNeedsTwo =>
      'Pick a target and at least one service to fold in.';

  @override
  String sourcesMergeDone(int count, String target) {
    return 'Merged $count service(s) into $target.';
  }

  @override
  String get sourcesFieldServiceId => 'Service id';

  @override
  String get sourcesFieldServiceIdHint =>
      'kebab-case, e.g. algorand-foundation-blog';

  @override
  String get sourcesFieldDisplayName => 'Display name';

  @override
  String get sourcesFieldScrapeUrl => 'Scrape URL';

  @override
  String get sourcesFieldScrapeUrlHint =>
      'https://…, reddit://r/…, discord://channel/…';

  @override
  String get sourcesFieldMatchKind => 'Match kind';

  @override
  String get sourcesFieldMatchKindHint => 'address / app_id / asset_id';

  @override
  String get sourcesFieldMatchValue => 'Match value';

  @override
  String get sourcesFieldMatchValueHint => 'e.g. wallet address or app id';

  @override
  String get sourcesMatchRuleHelp =>
      'The match rule links this source to on-chain activity: when the chain crawler sees a MainNet transaction whose sender/receiver address (kind \"address\"), application id (\"app_id\") or asset id (\"asset_id\") equals the match value, it attributes the event to this source and can trigger an article. For web or Reddit sources it is purely informational — use something descriptive like the domain or subreddit.';

  @override
  String get sourcesEnabled => 'Enabled';

  @override
  String get actionCancel => 'Cancel';

  @override
  String get actionRefresh => 'Refresh';

  @override
  String get adminTabSeeds => 'Seeds';

  @override
  String get adminTabArticles => 'Articles';

  @override
  String get adminTabWriterBriefs => 'Writer briefs';

  @override
  String get adminTabClassifier => 'Classifier';

  @override
  String get adminTabDomains => 'Domains';

  @override
  String get adminTabToolInsights => 'Tool insights';

  @override
  String get adminTabSessions => 'Sessions';

  @override
  String get adminTabSystem => 'System';

  @override
  String get domainsIntro =>
      'Crawl frontier: domains the link crawler has met. Dead ends are never explored — set automatically by relevance scoring and your review verdicts, or manually here.';

  @override
  String get domainsFilterAll => 'All';

  @override
  String domainsFilterAllCount(int count) {
    return 'All ($count)';
  }

  @override
  String get domainsFilterPending => 'Pending review';

  @override
  String domainsFilterPendingCount(int count) {
    return 'Pending review ($count)';
  }

  @override
  String get domainsFilterDeadEnds => 'Dead ends';

  @override
  String domainsFilterDeadEndsCount(int count) {
    return 'Dead ends ($count)';
  }

  @override
  String get domainsEmptyTitle => 'No domains yet';

  @override
  String get domainsEmptyMessage =>
      'The crawler records every domain it meets while following links.';

  @override
  String domainsOpenInNewTab(String domain) {
    return 'Open $domain in a new tab';
  }

  @override
  String domainsKeywords(String keywords) {
    return 'keywords: $keywords';
  }

  @override
  String domainsLinkedAs(String text) {
    return 'linked as \"$text\"';
  }

  @override
  String domainsPredictedInterest(String score) {
    return 'predicted interest $score';
  }

  @override
  String domainsFoundOn(String url) {
    return 'found on $url';
  }

  @override
  String domainsScore(String score) {
    return 'score $score';
  }

  @override
  String domainsCrawled(String date) {
    return 'crawled $date';
  }

  @override
  String domainsPagesCrawled(int count) {
    return '$count pages';
  }

  @override
  String get domainsDeadEnd => 'Dead end';

  @override
  String get domainsApproveExplore => 'Approve & explore';

  @override
  String get domainsMarkDeadEnd => 'Mark dead end';

  @override
  String get domainsRevive => 'Revive';

  @override
  String domainsApprovedSnack(String domain) {
    return '$domain approved — the crawler may explore it';
  }

  @override
  String domainsDeadEndSnack(String domain) {
    return '$domain marked as dead end';
  }

  @override
  String get domainsWalletNotConnected => 'Wallet not connected';

  @override
  String get frontPageMoreNews => 'More news';

  @override
  String storiesCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count stories',
      one: '1 story',
    );
    return '$_temp0';
  }

  @override
  String get byTheNumbersRange => 'Past 7 days';

  @override
  String get byTheNumbersMarketCap => 'Market cap';

  @override
  String get byTheNumbersVolume => '24h volume';

  @override
  String get hotTabHot => 'Hot right now';

  @override
  String get hotTabAllTime => 'All-time';
}
