// ignore: unused_import
import 'package:intl/intl.dart' as intl;
import 'app_localizations.dart';

// ignore_for_file: type=lint

/// The translations for French (`fr`).
class AppLocalizationsFr extends AppLocalizations {
  AppLocalizationsFr([String locale = 'fr']) : super(locale);

  @override
  String get appTitle => 'PXke Algorand Projects';

  @override
  String get appTagline => 'Actualités, sources et communauté';

  @override
  String get navHome => 'Accueil';

  @override
  String get navNews => 'Actualités';

  @override
  String get navSources => 'Sources';

  @override
  String get navSuggestions => 'Suggestions';

  @override
  String get navSearch => 'Recherche';

  @override
  String get navAdmin => 'Administration';

  @override
  String get navProducts => 'PRODUITS';

  @override
  String get navWallet => 'Portefeuille';

  @override
  String get navAppearance => 'APPARENCE';

  @override
  String get pageTitleHome => 'PXke Algorand Projects';

  @override
  String get pageTitleArticle => 'Article';

  @override
  String get pageTitleNews => 'Actualités';

  @override
  String get pageTitleSources => 'Sources d\'actualités';

  @override
  String get pageTitleSuggestions => 'Suggestions';

  @override
  String get pageTitleSearch => 'Recherche';

  @override
  String get pageTitleAdmin => 'Administration';

  @override
  String get backToFeed => 'Retour au fil';

  @override
  String get homeWelcome => 'PXke, plateforme écosystème Algorand';

  @override
  String get homeTagline =>
      'Actualités, suggestions communautaires et recherche.';

  @override
  String get homeNewsDescription =>
      'Fil d\'articles publiés lorsque les sources ou la chaîne changent.';

  @override
  String get homeSourcesDescription =>
      'Registered Discord, Reddit, and web crawlers that power the news pipeline.';

  @override
  String get homeSuggestionsDescription =>
      'Proposez et votez des idées avec votre portefeuille.';

  @override
  String get homeSearchDescription =>
      'Recherche plein texte dans les articles publiés.';

  @override
  String get homeOpenProduct => 'Ouvrir';

  @override
  String get themeLight => 'Clair';

  @override
  String get themeDark => 'Sombre';

  @override
  String get themeSystem => 'Système';

  @override
  String get themeSwitchToLight => 'Switch to light theme';

  @override
  String get themeSwitchToDark => 'Switch to dark theme';

  @override
  String get localeSystem => 'Langue du système';

  @override
  String get localeEnglish => 'English';

  @override
  String get localeSpanish => 'Español';

  @override
  String get localeFrench => 'Français';

  @override
  String get localeArabic => 'العربية';

  @override
  String get navLanguage => 'LANGUE';

  @override
  String get walletConnected => 'Connecté';

  @override
  String get walletDisconnect => 'Déconnecter';

  @override
  String get walletSignInTitle => 'Sign in with wallet';

  @override
  String get walletSignInBody =>
      'Connect a WalletConnect-compatible Algorand wallet to submit suggestions and upvotes.';

  @override
  String get walletConnect => 'Connecter le portefeuille';

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
      'Portefeuille connecté. Retournez dans votre application portefeuille pour approuver la demande de connexion, puis revenez ici.';

  @override
  String get walletAwaitingApprovalTitle => 'Presque fini';

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
  String get articleViewOnExplorer => 'Voir sur l\'explorateur Algorand';

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
      'Connectez un portefeuille administrateur. Définissez ADMIN_WALLET_ADDRESSES à la compilation.';

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
  String get searchTitle => 'Recherche';

  @override
  String get searchSubtitle =>
      'Recherche Typesense si configurée, sinon scan du fil récent.';

  @override
  String get searchQueryLabel => 'Requête';

  @override
  String get searchQueryHint => 'Mots-clés, titres, résumés…';

  @override
  String get searchAction => 'Rechercher';

  @override
  String searchEngine(String name) {
    return 'Engine: $name';
  }

  @override
  String get searchEmptyTitle => 'Aucun résultat';

  @override
  String get searchEmptyMessage =>
      'Essayez d\'autres mots-clés ou vérifiez l\'indexation.';

  @override
  String get searchErrorBackend =>
      'Recherche indisponible. Vérifiez que l\'API et la base de données sont démarrées.';

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
    return 'Service : $serviceId';
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
  String get seedsTitle => 'Graines';

  @override
  String get seedsSubtitle =>
      'Points de départ configurés manuellement pour le crawler. Les domaines approuvés via la découverte figurent dans l’onglet Domaines.';

  @override
  String get sourcesAdd => 'Ajouter une source';

  @override
  String get sourcesEdit => 'Modifier';

  @override
  String get sourcesEditTitle => 'Modifier la source';

  @override
  String get sourcesAddTitle => 'Ajouter une source';

  @override
  String get sourcesDeleteTitle => 'Supprimer la source ?';

  @override
  String sourcesDeleteBody(String serviceId) {
    return '« $serviceId » ne sera plus explorée. Les articles déjà publiés restent en ligne.';
  }

  @override
  String get sourcesDelete => 'Supprimer';

  @override
  String get sourcesSave => 'Enregistrer';

  @override
  String get sourcesAddAction => 'Ajouter';

  @override
  String get sourcesRequiredFields =>
      'L’identifiant, le nom et l’URL sont obligatoires.';

  @override
  String get sourcesChangesNextPoll =>
      'Les modifications s’appliquent au prochain passage du crawler.';

  @override
  String get sourcesFieldServiceId => 'Identifiant de service';

  @override
  String get sourcesFieldServiceIdHint =>
      'kebab-case, ex. algorand-foundation-blog';

  @override
  String get sourcesFieldDisplayName => 'Nom affiché';

  @override
  String get sourcesFieldScrapeUrl => 'URL de crawl';

  @override
  String get sourcesFieldScrapeUrlHint =>
      'https://…, reddit://r/…, discord://channel/…';

  @override
  String get sourcesFieldMatchKind => 'Type de correspondance';

  @override
  String get sourcesFieldMatchKindHint => 'address / app_id / asset_id';

  @override
  String get sourcesFieldMatchValue => 'Valeur de correspondance';

  @override
  String get sourcesFieldMatchValueHint =>
      'ex. adresse de portefeuille ou id d’app';

  @override
  String get sourcesMatchRuleHelp =>
      'La règle de correspondance relie cette source à l’activité on-chain : lorsque le crawler voit une transaction MainNet dont l’adresse émettrice/réceptrice (type « address »), l’id d’application (« app_id ») ou l’id d’actif (« asset_id ») correspond à la valeur, il attribue l’événement à cette source et peut déclencher un article. Pour le web ou Reddit, c’est purement informatif — utilisez le domaine ou le subreddit par exemple.';

  @override
  String get sourcesEnabled => 'Activé';

  @override
  String get actionCancel => 'Annuler';

  @override
  String get actionRefresh => 'Actualiser';

  @override
  String get adminTabSeeds => 'Graines';

  @override
  String get adminTabArticles => 'Articles';

  @override
  String get adminTabWriterBriefs => 'Briefs rédaction';

  @override
  String get adminTabClassifier => 'Classifieur';

  @override
  String get adminTabDomains => 'Domaines';

  @override
  String get adminTabToolInsights => 'Outils';

  @override
  String get adminTabSessions => 'Sessions';

  @override
  String get adminTabSystem => 'Système';

  @override
  String get domainsIntro =>
      'Frontière de crawl : domaines rencontrés par le crawler de liens. Les impasses ne sont jamais explorées — elles sont définies par le score de pertinence, vos verdicts ou manuellement ici.';

  @override
  String get domainsFilterAll => 'Tous';

  @override
  String domainsFilterAllCount(int count) {
    return 'Tous ($count)';
  }

  @override
  String get domainsFilterPending => 'En attente de revue';

  @override
  String domainsFilterPendingCount(int count) {
    return 'En attente de revue ($count)';
  }

  @override
  String get domainsFilterDeadEnds => 'Impasses';

  @override
  String domainsFilterDeadEndsCount(int count) {
    return 'Impasses ($count)';
  }

  @override
  String get domainsEmptyTitle => 'Aucun domaine pour l’instant';

  @override
  String get domainsEmptyMessage =>
      'Le crawler enregistre chaque domaine rencontré en suivant les liens.';

  @override
  String domainsOpenInNewTab(String domain) {
    return 'Ouvrir $domain dans un nouvel onglet';
  }

  @override
  String domainsKeywords(String keywords) {
    return 'mots-clés : $keywords';
  }

  @override
  String domainsLinkedAs(String text) {
    return 'lié comme « $text »';
  }

  @override
  String domainsPredictedInterest(String score) {
    return 'intérêt prévu $score';
  }

  @override
  String domainsFoundOn(String url) {
    return 'trouvé sur $url';
  }

  @override
  String domainsScore(String score) {
    return 'score $score';
  }

  @override
  String domainsCrawled(String date) {
    return 'exploré le $date';
  }

  @override
  String domainsPagesCrawled(int count) {
    return '$count pages';
  }

  @override
  String get domainsDeadEnd => 'Impasse';

  @override
  String get domainsApproveExplore => 'Approuver et explorer';

  @override
  String get domainsMarkDeadEnd => 'Marquer comme impasse';

  @override
  String get domainsRevive => 'Réactiver';

  @override
  String domainsApprovedSnack(String domain) {
    return '$domain approuvé — le crawler peut l’explorer';
  }

  @override
  String domainsDeadEndSnack(String domain) {
    return '$domain marqué comme impasse';
  }

  @override
  String get domainsWalletNotConnected => 'Portefeuille non connecté';
}
