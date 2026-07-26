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
  String get localeChinese => 'Chine';

  @override
  String get localeHindi => 'हिन्दी';

  @override
  String get localeRussian => 'Русский';

  @override
  String get localeDari => 'دری';

  @override
  String get localePashto => 'پښتو';

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
  String get walletErrorTitle => 'Échec de la connexion';

  @override
  String get walletErrorTimeout =>
      'La demande de connexion a expiré. Ouvrez votre portefeuille et réessayez.';

  @override
  String get walletErrorRejected =>
      'La demande a été refusée dans le portefeuille.';

  @override
  String get walletErrorGeneric =>
      'Impossible de finaliser la connexion. Vérifiez votre réseau et réessayez.';

  @override
  String get walletRetry => 'Réessayer';

  @override
  String get walletShowQr => 'Afficher le code QR';

  @override
  String get walletMobileHint =>
      'Touchez le bouton ci-dessous pour ouvrir votre portefeuille Algorand, approuvez la connexion, puis revenez ici.';

  @override
  String get walletSignExplainer =>
      'Votre portefeuille vous demandera de signer un message de connexion (les anciens portefeuilles affichent une transaction de 0 ALGO). La signature est gratuite et rien n\'est envoyé au réseau — elle prouve seulement que l\'adresse vous appartient.';

  @override
  String get newsFeedTitle => 'Derniers articles';

  @override
  String get newsSubtitleDefault =>
      'On-chain triggers and crawled sources publish here when content changes.';

  @override
  String get articlePublicationDetailsHint => 'Éditeur, date et lien source';

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
  String get navContact => 'Contact';

  @override
  String get contactTitle => 'Contact';

  @override
  String get contactSubtitle =>
      'Corrections, informations ou remarques — écrivez à la rédaction.';

  @override
  String get contactNameLabel => 'Votre nom (facultatif)';

  @override
  String get contactEmailLabel =>
      'E-mail (facultatif, si vous souhaitez une réponse)';

  @override
  String get contactMessageLabel => 'Message';

  @override
  String get contactMessageHint => 'Corrections, informations, remarques…';

  @override
  String get contactSend => 'Envoyer le message';

  @override
  String get contactSent => 'Message envoyé — merci de nous avoir écrit.';

  @override
  String get contactTooShort =>
      'Merci d\'écrire quelques mots de plus (au moins 10 caractères).';

  @override
  String get searchTitle => 'Recherche';

  @override
  String get searchSubtitle => 'Recherchez parmi tous nos articles publiés.';

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
  String get navFrontPage => 'Une';

  @override
  String get navLatest => 'Dernières';

  @override
  String get navSections => 'RUBRIQUES';

  @override
  String get navAbout => 'À propos';

  @override
  String get navApps => 'Apps';

  @override
  String get navProductsMenuHint => 'Explorer la plateforme';

  @override
  String get frontPageTopStories => 'À la une';

  @override
  String get frontPageLatest => 'Dernières';

  @override
  String get frontPageMore => 'Plus de la rédaction';

  @override
  String frontPageSectionStories(String section) {
    return 'Plus dans $section';
  }

  @override
  String get navHot => 'Populaire';

  @override
  String get navTopics => 'Sujets';

  @override
  String get hotTitle => 'Les plus lus';

  @override
  String get hotLead =>
      'Les articles que les lecteurs ouvrent le plus en ce moment.';

  @override
  String get topicsTitle => 'Sujets';

  @override
  String get topicsLead =>
      'Chaque étiquette utilisée récemment par la rédaction — la taille reflète la couverture, la couleur les lectures.';

  @override
  String topicSubtitle(String tag) {
    return 'Articles étiquetés « $tag »';
  }

  @override
  String readsCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count lectures',
      one: '1 lecture',
    );
    return '$_temp0';
  }

  @override
  String get sectionMarkets => 'Marchés';

  @override
  String get sectionSecurity => 'Sécurité';

  @override
  String get sectionDevelopers => 'Développeurs';

  @override
  String get sectionCommunity => 'Communauté';

  @override
  String get sectionEcosystem => 'Écosystème';

  @override
  String get sectionEmptyTitle => 'Rien ici pour l\'instant';

  @override
  String get sectionEmptyMessage =>
      'Aucun article dans cette rubrique pour l\'instant. Revenez bientôt.';

  @override
  String get bylineNewsroom => 'La rédaction';

  @override
  String get bylineMarketsDesk => 'Rédaction Marchés';

  @override
  String get bylineChainDesk => 'Rédaction On-Chain';

  @override
  String articleByline(String desk) {
    return 'Par $desk';
  }

  @override
  String articleReadingTime(int count) {
    return '$count min de lecture';
  }

  @override
  String get articleRelatedTitle => 'Articles connexes';

  @override
  String get articleShare => 'Partager';

  @override
  String get articleShareCopyLink => 'Copier le lien';

  @override
  String get articleLinkCopied => 'Lien copié dans le presse-papiers';

  @override
  String get footerTagline =>
      'Couverture indépendante et automatisée de l\'écosystème Algorand.';

  @override
  String get footerSectionsHeading => 'Rubriques';

  @override
  String get footerAboutHeading => 'À propos';

  @override
  String get footerFollowHeading => 'Suivez-nous';

  @override
  String footerRights(String year) {
    return '© $year PXke Algorand. Couverture indépendante de l\'écosystème Algorand.';
  }

  @override
  String get aboutTitle => 'À propos de PXke Algorand';

  @override
  String get aboutLead =>
      'PXke Algorand est une rédaction indépendante qui couvre la blockchain Algorand — marchés, protocole, gouvernance et projets qui s\'y construisent.';

  @override
  String get aboutHowHeading => 'Comment nous publions';

  @override
  String get aboutHowBody =>
      'Notre couverture est assemblée automatiquement à partir d\'événements on-chain, de données de marché planifiées et de sources communautaires surveillées, puis organisée en rubriques. Chaque article indique sa provenance.';

  @override
  String get aboutAiHeading => 'Rédigé avec l\'IA';

  @override
  String get aboutAiBody =>
      'PXke Algorand publie du journalisme assisté par IA. Les articles sont rédigés par des modèles de langage à partir d\'événements on-chain, de données de marché et de sources communautaires, sous revue éditoriale automatisée.';

  @override
  String get aboutProvenanceHeading => 'Provenance et transparence';

  @override
  String get aboutProvenanceBody =>
      'Les articles on-chain sont déclenchés par des transactions vérifiables et renvoient vers l\'explorateur Algorand. Les articles marché s\'appuient sur CoinGecko. Les contenus sponsorisés sont toujours signalés.';

  @override
  String get aboutStandardsHeading => 'Standards éditoriaux';

  @override
  String get aboutStandardsBody =>
      'Nous visons l\'exactitude et une attribution claire. Cette publication n\'est pas un conseil en investissement. Une erreur ? Contactez-nous via les liens source de n\'importe quel article.';

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
  String get sourcesMerge => 'Fusionner';

  @override
  String get sourcesMergeTitle => 'Fusionner des services';

  @override
  String get sourcesMergeIntro =>
      'Regroupez plusieurs services en un. Les sources et domaines des services choisis passent à la cible ; les services vidés sont désactivés.';

  @override
  String get sourcesMergeTarget => 'Conserver comme cible';

  @override
  String get sourcesMergeFold => 'Inclure (sera désactivé)';

  @override
  String get sourcesMergeAction => 'Fusionner';

  @override
  String get sourcesMergeNeedsTwo =>
      'Choisissez une cible et au moins un service à inclure.';

  @override
  String sourcesMergeDone(int count, String target) {
    return '$count service(s) fusionné(s) dans $target.';
  }

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
  String get domainsApproveExplore => 'Site complet';

  @override
  String get domainsAddButton => 'Ajouter';

  @override
  String get domainsAddSinglePageOnly => 'Page unique';

  @override
  String get domainsScoreUnexplained => 'Détail du score non disponible';

  @override
  String get domainsPossibleService => 'service possible ?';

  @override
  String get domainsPossibleServiceHint =>
      'Bien noté mais classé actualité/générique — pourrait être un vrai produit, pas juste une source de citation. Envisagez « Site complet » plutôt que « Page unique ».';

  @override
  String domainsSuggestedHint(int count) {
    return 'Suggéré — $count pages du même domaine trouvées';
  }

  @override
  String get paginationPrevious => 'Précédent';

  @override
  String get paginationNext => 'Suivant';

  @override
  String paginationPageOf(int page, int total) {
    return 'Page $page sur $total';
  }

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

  @override
  String get frontPageMoreNews => 'Plus d\'actualités';

  @override
  String storiesCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count articles',
      one: '1 article',
    );
    return '$_temp0';
  }

  @override
  String get byTheNumbersRange => '7 derniers jours';

  @override
  String get byTheNumbersMarketCap => 'Capitalisation';

  @override
  String get byTheNumbersVolume => 'Volume 24h';

  @override
  String get hotTabHot => 'Tendances';

  @override
  String get hotTabAllTime => 'Depuis toujours';
}
