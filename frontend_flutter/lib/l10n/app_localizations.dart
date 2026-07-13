import 'dart:async';

import 'package:flutter/widgets.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:intl/intl.dart' as intl;

import 'app_localizations_ar.dart' deferred as app_localizations_ar;
import 'app_localizations_en.dart' deferred as app_localizations_en;
import 'app_localizations_es.dart' deferred as app_localizations_es;
import 'app_localizations_fa.dart' deferred as app_localizations_fa;
import 'app_localizations_fr.dart' deferred as app_localizations_fr;
import 'app_localizations_hi.dart' deferred as app_localizations_hi;
import 'app_localizations_ps.dart' deferred as app_localizations_ps;
import 'app_localizations_ru.dart' deferred as app_localizations_ru;
import 'app_localizations_zh.dart' deferred as app_localizations_zh;

// ignore_for_file: type=lint

/// Callers can lookup localized strings with an instance of AppLocalizations
/// returned by `AppLocalizations.of(context)`.
///
/// Applications need to include `AppLocalizations.delegate()` in their app's
/// `localizationDelegates` list, and the locales they support in the app's
/// `supportedLocales` list. For example:
///
/// ```dart
/// import 'l10n/app_localizations.dart';
///
/// return MaterialApp(
///   localizationsDelegates: AppLocalizations.localizationsDelegates,
///   supportedLocales: AppLocalizations.supportedLocales,
///   home: MyApplicationHome(),
/// );
/// ```
///
/// ## Update pubspec.yaml
///
/// Please make sure to update your pubspec.yaml to include the following
/// packages:
///
/// ```yaml
/// dependencies:
///   # Internationalization support.
///   flutter_localizations:
///     sdk: flutter
///   intl: any # Use the pinned version from flutter_localizations
///
///   # Rest of dependencies
/// ```
///
/// ## iOS Applications
///
/// iOS applications define key application metadata, including supported
/// locales, in an Info.plist file that is built into the application bundle.
/// To configure the locales supported by your app, you’ll need to edit this
/// file.
///
/// First, open your project’s ios/Runner.xcworkspace Xcode workspace file.
/// Then, in the Project Navigator, open the Info.plist file under the Runner
/// project’s Runner folder.
///
/// Next, select the Information Property List item, select Add Item from the
/// Editor menu, then select Localizations from the pop-up menu.
///
/// Select and expand the newly-created Localizations item then, for each
/// locale your application supports, add a new item and select the locale
/// you wish to add from the pop-up menu in the Value field. This list should
/// be consistent with the languages listed in the AppLocalizations.supportedLocales
/// property.
abstract class AppLocalizations {
  AppLocalizations(String locale)
    : localeName = intl.Intl.canonicalizedLocale(locale.toString());

  final String localeName;

  static AppLocalizations of(BuildContext context) {
    return Localizations.of<AppLocalizations>(context, AppLocalizations)!;
  }

  static const LocalizationsDelegate<AppLocalizations> delegate =
      _AppLocalizationsDelegate();

  /// A list of this localizations delegate along with the default localizations
  /// delegates.
  ///
  /// Returns a list of localizations delegates containing this delegate along with
  /// GlobalMaterialLocalizations.delegate, GlobalCupertinoLocalizations.delegate,
  /// and GlobalWidgetsLocalizations.delegate.
  ///
  /// Additional delegates can be added by appending to this list in
  /// MaterialApp. This list does not have to be used at all if a custom list
  /// of delegates is preferred or required.
  static const List<LocalizationsDelegate<dynamic>> localizationsDelegates =
      <LocalizationsDelegate<dynamic>>[
        delegate,
        GlobalMaterialLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
      ];

  /// A list of this localizations delegate's supported locales.
  static const List<Locale> supportedLocales = <Locale>[
    Locale('ar'),
    Locale('en'),
    Locale('es'),
    Locale('fa'),
    Locale('fr'),
    Locale('hi'),
    Locale('ps'),
    Locale('ru'),
    Locale('zh'),
  ];

  /// No description provided for @appTitle.
  ///
  /// In en, this message translates to:
  /// **'PXke Algorand'**
  String get appTitle;

  /// No description provided for @appTagline.
  ///
  /// In en, this message translates to:
  /// **'Independent coverage of the Algorand ecosystem'**
  String get appTagline;

  /// No description provided for @navHome.
  ///
  /// In en, this message translates to:
  /// **'Home'**
  String get navHome;

  /// No description provided for @navNews.
  ///
  /// In en, this message translates to:
  /// **'News'**
  String get navNews;

  /// No description provided for @navSources.
  ///
  /// In en, this message translates to:
  /// **'Sources'**
  String get navSources;

  /// No description provided for @navSuggestions.
  ///
  /// In en, this message translates to:
  /// **'Suggestions'**
  String get navSuggestions;

  /// No description provided for @navSearch.
  ///
  /// In en, this message translates to:
  /// **'Search'**
  String get navSearch;

  /// No description provided for @navAdmin.
  ///
  /// In en, this message translates to:
  /// **'Admin'**
  String get navAdmin;

  /// No description provided for @navProducts.
  ///
  /// In en, this message translates to:
  /// **'PRODUCTS'**
  String get navProducts;

  /// No description provided for @navWallet.
  ///
  /// In en, this message translates to:
  /// **'Wallet'**
  String get navWallet;

  /// No description provided for @navAppearance.
  ///
  /// In en, this message translates to:
  /// **'APPEARANCE'**
  String get navAppearance;

  /// No description provided for @pageTitleHome.
  ///
  /// In en, this message translates to:
  /// **'PXke Algorand Projects'**
  String get pageTitleHome;

  /// No description provided for @pageTitleArticle.
  ///
  /// In en, this message translates to:
  /// **'Article'**
  String get pageTitleArticle;

  /// No description provided for @pageTitleNews.
  ///
  /// In en, this message translates to:
  /// **'News'**
  String get pageTitleNews;

  /// No description provided for @pageTitleSources.
  ///
  /// In en, this message translates to:
  /// **'News sources'**
  String get pageTitleSources;

  /// No description provided for @pageTitleSuggestions.
  ///
  /// In en, this message translates to:
  /// **'Suggestions'**
  String get pageTitleSuggestions;

  /// No description provided for @pageTitleSearch.
  ///
  /// In en, this message translates to:
  /// **'Search'**
  String get pageTitleSearch;

  /// No description provided for @pageTitleAdmin.
  ///
  /// In en, this message translates to:
  /// **'Admin'**
  String get pageTitleAdmin;

  /// No description provided for @backToFeed.
  ///
  /// In en, this message translates to:
  /// **'Back to feed'**
  String get backToFeed;

  /// No description provided for @homeWelcome.
  ///
  /// In en, this message translates to:
  /// **'PXke Algorand ecosystem platform'**
  String get homeWelcome;

  /// No description provided for @homeTagline.
  ///
  /// In en, this message translates to:
  /// **'Curated news from on-chain triggers and crawled sources, community suggestions, and search.'**
  String get homeTagline;

  /// No description provided for @homeNewsDescription.
  ///
  /// In en, this message translates to:
  /// **'Curated feed of articles published when monitored sources or chain events change.'**
  String get homeNewsDescription;

  /// No description provided for @homeSourcesDescription.
  ///
  /// In en, this message translates to:
  /// **'Registered Discord, Reddit, and web crawlers that power the news pipeline.'**
  String get homeSourcesDescription;

  /// No description provided for @homeSuggestionsDescription.
  ///
  /// In en, this message translates to:
  /// **'Submit and upvote community ideas with wallet-backed authentication.'**
  String get homeSuggestionsDescription;

  /// No description provided for @homeSearchDescription.
  ///
  /// In en, this message translates to:
  /// **'Full-text search across published articles.'**
  String get homeSearchDescription;

  /// No description provided for @homeOpenProduct.
  ///
  /// In en, this message translates to:
  /// **'Open'**
  String get homeOpenProduct;

  /// No description provided for @themeLight.
  ///
  /// In en, this message translates to:
  /// **'Light'**
  String get themeLight;

  /// No description provided for @themeDark.
  ///
  /// In en, this message translates to:
  /// **'Dark'**
  String get themeDark;

  /// No description provided for @themeSystem.
  ///
  /// In en, this message translates to:
  /// **'System'**
  String get themeSystem;

  /// No description provided for @themeSwitchToLight.
  ///
  /// In en, this message translates to:
  /// **'Switch to light theme'**
  String get themeSwitchToLight;

  /// No description provided for @themeSwitchToDark.
  ///
  /// In en, this message translates to:
  /// **'Switch to dark theme'**
  String get themeSwitchToDark;

  /// No description provided for @localeSystem.
  ///
  /// In en, this message translates to:
  /// **'System language'**
  String get localeSystem;

  /// No description provided for @localeEnglish.
  ///
  /// In en, this message translates to:
  /// **'English'**
  String get localeEnglish;

  /// No description provided for @localeSpanish.
  ///
  /// In en, this message translates to:
  /// **'Español'**
  String get localeSpanish;

  /// No description provided for @localeFrench.
  ///
  /// In en, this message translates to:
  /// **'Français'**
  String get localeFrench;

  /// No description provided for @localeArabic.
  ///
  /// In en, this message translates to:
  /// **'العربية'**
  String get localeArabic;

  /// No description provided for @localeChinese.
  ///
  /// In en, this message translates to:
  /// **'中文'**
  String get localeChinese;

  /// No description provided for @localeHindi.
  ///
  /// In en, this message translates to:
  /// **'हिन्दी'**
  String get localeHindi;

  /// No description provided for @localeRussian.
  ///
  /// In en, this message translates to:
  /// **'Русский'**
  String get localeRussian;

  /// No description provided for @localeDari.
  ///
  /// In en, this message translates to:
  /// **'دری'**
  String get localeDari;

  /// No description provided for @localePashto.
  ///
  /// In en, this message translates to:
  /// **'پښتو'**
  String get localePashto;

  /// No description provided for @navLanguage.
  ///
  /// In en, this message translates to:
  /// **'LANGUAGE'**
  String get navLanguage;

  /// No description provided for @walletConnected.
  ///
  /// In en, this message translates to:
  /// **'Connected'**
  String get walletConnected;

  /// No description provided for @walletDisconnect.
  ///
  /// In en, this message translates to:
  /// **'Disconnect'**
  String get walletDisconnect;

  /// No description provided for @walletSignInTitle.
  ///
  /// In en, this message translates to:
  /// **'Sign in with wallet'**
  String get walletSignInTitle;

  /// No description provided for @walletSignInBody.
  ///
  /// In en, this message translates to:
  /// **'Connect a WalletConnect-compatible Algorand wallet to submit suggestions and upvotes.'**
  String get walletSignInBody;

  /// No description provided for @walletConnect.
  ///
  /// In en, this message translates to:
  /// **'Connect wallet'**
  String get walletConnect;

  /// No description provided for @walletConnectFailed.
  ///
  /// In en, this message translates to:
  /// **'Connection failed. Try again or cancel the pairing dialog first.'**
  String get walletConnectFailed;

  /// No description provided for @walletDialogTitle.
  ///
  /// In en, this message translates to:
  /// **'Connect your wallet'**
  String get walletDialogTitle;

  /// No description provided for @walletDialogBody.
  ///
  /// In en, this message translates to:
  /// **'Scan the QR code with a WalletConnect-compatible Algorand wallet (Pera, Defly, etc.) or copy/open the link on mobile.'**
  String get walletDialogBody;

  /// No description provided for @walletAwaitingApproval.
  ///
  /// In en, this message translates to:
  /// **'Wallet connected. Switch back to your wallet app to approve the sign-in request, then return here.'**
  String get walletAwaitingApproval;

  /// No description provided for @walletAwaitingApprovalTitle.
  ///
  /// In en, this message translates to:
  /// **'Almost there'**
  String get walletAwaitingApprovalTitle;

  /// No description provided for @walletCopyUri.
  ///
  /// In en, this message translates to:
  /// **'Copy URI'**
  String get walletCopyUri;

  /// No description provided for @walletUriCopied.
  ///
  /// In en, this message translates to:
  /// **'WalletConnect URI copied'**
  String get walletUriCopied;

  /// No description provided for @walletOpenWallet.
  ///
  /// In en, this message translates to:
  /// **'Open wallet'**
  String get walletOpenWallet;

  /// No description provided for @walletOpenFailed.
  ///
  /// In en, this message translates to:
  /// **'Could not open wallet app — copy the URI and paste it in Pera or Defly'**
  String get walletOpenFailed;

  /// No description provided for @walletCancel.
  ///
  /// In en, this message translates to:
  /// **'Cancel'**
  String get walletCancel;

  /// No description provided for @walletDone.
  ///
  /// In en, this message translates to:
  /// **'Done'**
  String get walletDone;

  /// No description provided for @walletErrorTitle.
  ///
  /// In en, this message translates to:
  /// **'Sign-in failed'**
  String get walletErrorTitle;

  /// No description provided for @walletErrorTimeout.
  ///
  /// In en, this message translates to:
  /// **'The sign-in request timed out. Open your wallet app and try again.'**
  String get walletErrorTimeout;

  /// No description provided for @walletErrorRejected.
  ///
  /// In en, this message translates to:
  /// **'The request was declined in the wallet.'**
  String get walletErrorRejected;

  /// No description provided for @walletErrorGeneric.
  ///
  /// In en, this message translates to:
  /// **'Could not complete the sign-in. Check your connection and try again.'**
  String get walletErrorGeneric;

  /// No description provided for @walletRetry.
  ///
  /// In en, this message translates to:
  /// **'Try again'**
  String get walletRetry;

  /// No description provided for @walletShowQr.
  ///
  /// In en, this message translates to:
  /// **'Show QR code'**
  String get walletShowQr;

  /// No description provided for @walletMobileHint.
  ///
  /// In en, this message translates to:
  /// **'Tap the button below to open your Algorand wallet, approve the connection, then return here.'**
  String get walletMobileHint;

  /// No description provided for @walletSignExplainer.
  ///
  /// In en, this message translates to:
  /// **'Your wallet will ask you to sign a sign-in message (older wallets show a 0-ALGO transaction instead). Signing is free and nothing is sent to the network — it only proves you own the address.'**
  String get walletSignExplainer;

  /// No description provided for @newsFeedTitle.
  ///
  /// In en, this message translates to:
  /// **'Latest articles'**
  String get newsFeedTitle;

  /// No description provided for @newsSubtitleDefault.
  ///
  /// In en, this message translates to:
  /// **'On-chain triggers and crawled sources publish here when content changes.'**
  String get newsSubtitleDefault;

  /// No description provided for @articlePublicationDetailsHint.
  ///
  /// In en, this message translates to:
  /// **'Publisher, date, and source link'**
  String get articlePublicationDetailsHint;

  /// No description provided for @newsPriceUnavailable.
  ///
  /// In en, this message translates to:
  /// **'ALGO price unavailable — run price metrics collection'**
  String get newsPriceUnavailable;

  /// No description provided for @newsArticleCount.
  ///
  /// In en, this message translates to:
  /// **'{count} articles in the curated feed'**
  String newsArticleCount(int count);

  /// No description provided for @newsEmptyFilteredTitle.
  ///
  /// In en, this message translates to:
  /// **'No articles yet'**
  String get newsEmptyFilteredTitle;

  /// No description provided for @newsEmptyTitle.
  ///
  /// In en, this message translates to:
  /// **'Feed is empty'**
  String get newsEmptyTitle;

  /// No description provided for @newsSponsoredLabel.
  ///
  /// In en, this message translates to:
  /// **'Sponsored'**
  String get newsSponsoredLabel;

  /// No description provided for @newsSponsoredBy.
  ///
  /// In en, this message translates to:
  /// **'Sponsored · {sponsor}'**
  String newsSponsoredBy(String sponsor);

  /// No description provided for @newsEmptyFilteredMessage.
  ///
  /// In en, this message translates to:
  /// **'This source has not published any articles. Check that workers are polling and content has changed.'**
  String get newsEmptyFilteredMessage;

  /// No description provided for @newsEmptyMessage.
  ///
  /// In en, this message translates to:
  /// **'Start Conduit, Celery workers, and seed service_registry to populate the feed.'**
  String get newsEmptyMessage;

  /// No description provided for @newsFilterShowing.
  ///
  /// In en, this message translates to:
  /// **'Showing articles from {serviceId}'**
  String newsFilterShowing(String serviceId);

  /// No description provided for @clearFilter.
  ///
  /// In en, this message translates to:
  /// **'Clear'**
  String get clearFilter;

  /// No description provided for @articleUntitled.
  ///
  /// In en, this message translates to:
  /// **'Untitled'**
  String get articleUntitled;

  /// No description provided for @articleSectionTitle.
  ///
  /// In en, this message translates to:
  /// **'Article'**
  String get articleSectionTitle;

  /// No description provided for @articlePublicationDetails.
  ///
  /// In en, this message translates to:
  /// **'Publication details'**
  String get articlePublicationDetails;

  /// No description provided for @articleMetaService.
  ///
  /// In en, this message translates to:
  /// **'Service'**
  String get articleMetaService;

  /// No description provided for @articleMetaPublisher.
  ///
  /// In en, this message translates to:
  /// **'Publisher'**
  String get articleMetaPublisher;

  /// No description provided for @articleMetaDataSource.
  ///
  /// In en, this message translates to:
  /// **'Data source'**
  String get articleMetaDataSource;

  /// No description provided for @articleMetaCoinGecko.
  ///
  /// In en, this message translates to:
  /// **'CoinGecko market data'**
  String get articleMetaCoinGecko;

  /// No description provided for @articleMetaPublished.
  ///
  /// In en, this message translates to:
  /// **'Published'**
  String get articleMetaPublished;

  /// No description provided for @articleMetaRound.
  ///
  /// In en, this message translates to:
  /// **'Round'**
  String get articleMetaRound;

  /// No description provided for @articleMetaTriggerTx.
  ///
  /// In en, this message translates to:
  /// **'Trigger tx'**
  String get articleMetaTriggerTx;

  /// No description provided for @articleMetaSourceUrl.
  ///
  /// In en, this message translates to:
  /// **'Source URL'**
  String get articleMetaSourceUrl;

  /// No description provided for @articleOpenInBrowser.
  ///
  /// In en, this message translates to:
  /// **'Open in browser'**
  String get articleOpenInBrowser;

  /// No description provided for @articleViewOnExplorer.
  ///
  /// In en, this message translates to:
  /// **'View on Algorand explorer'**
  String get articleViewOnExplorer;

  /// No description provided for @articleMoreFrom.
  ///
  /// In en, this message translates to:
  /// **'More from {serviceId}'**
  String articleMoreFrom(String serviceId);

  /// No description provided for @articleViewSource.
  ///
  /// In en, this message translates to:
  /// **'View source'**
  String get articleViewSource;

  /// No description provided for @adminTitle.
  ///
  /// In en, this message translates to:
  /// **'Source administration'**
  String get adminTitle;

  /// No description provided for @adminSubtitle.
  ///
  /// In en, this message translates to:
  /// **'Registered crawlers and feeds (visible to admin wallets only).'**
  String get adminSubtitle;

  /// No description provided for @adminViewFeedArticles.
  ///
  /// In en, this message translates to:
  /// **'View feed for this source'**
  String get adminViewFeedArticles;

  /// No description provided for @adminAccessDenied.
  ///
  /// In en, this message translates to:
  /// **'Connect an admin wallet to manage sources. Set ADMIN_WALLET_ADDRESSES at build time.'**
  String get adminAccessDenied;

  /// No description provided for @sourcesTitle.
  ///
  /// In en, this message translates to:
  /// **'News sources'**
  String get sourcesTitle;

  /// No description provided for @sourcesSubtitle.
  ///
  /// In en, this message translates to:
  /// **'Registered services crawled by workers. Discord and Reddit are polled on a schedule; on-chain matches trigger publishes when monitored content changes.'**
  String get sourcesSubtitle;

  /// No description provided for @sourcesEmptyTitle.
  ///
  /// In en, this message translates to:
  /// **'No sources configured'**
  String get sourcesEmptyTitle;

  /// No description provided for @sourcesEmptyMessage.
  ///
  /// In en, this message translates to:
  /// **'Seed service_registry with Discord and Reddit TOML files, then run migrations. Sources appear here once registered in Cassandra.'**
  String get sourcesEmptyMessage;

  /// No description provided for @sourcesMetaServiceId.
  ///
  /// In en, this message translates to:
  /// **'Service ID'**
  String get sourcesMetaServiceId;

  /// No description provided for @sourcesMetaScrapeUrl.
  ///
  /// In en, this message translates to:
  /// **'Scrape URL'**
  String get sourcesMetaScrapeUrl;

  /// No description provided for @sourcesMetaMatchRule.
  ///
  /// In en, this message translates to:
  /// **'Match rule'**
  String get sourcesMetaMatchRule;

  /// No description provided for @sourcesViewArticles.
  ///
  /// In en, this message translates to:
  /// **'View articles'**
  String get sourcesViewArticles;

  /// No description provided for @sourcesDisabled.
  ///
  /// In en, this message translates to:
  /// **'Disabled'**
  String get sourcesDisabled;

  /// No description provided for @filterAll.
  ///
  /// In en, this message translates to:
  /// **'All ({count})'**
  String filterAll(int count);

  /// No description provided for @filterDiscord.
  ///
  /// In en, this message translates to:
  /// **'Discord ({count})'**
  String filterDiscord(int count);

  /// No description provided for @filterReddit.
  ///
  /// In en, this message translates to:
  /// **'Reddit ({count})'**
  String filterReddit(int count);

  /// No description provided for @filterWeb.
  ///
  /// In en, this message translates to:
  /// **'Web ({count})'**
  String filterWeb(int count);

  /// No description provided for @matchRuleValue.
  ///
  /// In en, this message translates to:
  /// **'{kind} = {value}'**
  String matchRuleValue(String kind, String value);

  /// No description provided for @sourceKindDiscord.
  ///
  /// In en, this message translates to:
  /// **'Discord'**
  String get sourceKindDiscord;

  /// No description provided for @sourceKindReddit.
  ///
  /// In en, this message translates to:
  /// **'Reddit'**
  String get sourceKindReddit;

  /// No description provided for @sourceKindWeb.
  ///
  /// In en, this message translates to:
  /// **'Web'**
  String get sourceKindWeb;

  /// No description provided for @sourceKindOnChain.
  ///
  /// In en, this message translates to:
  /// **'On-chain'**
  String get sourceKindOnChain;

  /// No description provided for @sourceKindUnknown.
  ///
  /// In en, this message translates to:
  /// **'Source'**
  String get sourceKindUnknown;

  /// No description provided for @navContact.
  ///
  /// In en, this message translates to:
  /// **'Contact'**
  String get navContact;

  /// No description provided for @contactTitle.
  ///
  /// In en, this message translates to:
  /// **'Contact'**
  String get contactTitle;

  /// No description provided for @contactSubtitle.
  ///
  /// In en, this message translates to:
  /// **'Corrections, tips or feedback — write to the newsroom.'**
  String get contactSubtitle;

  /// No description provided for @contactNameLabel.
  ///
  /// In en, this message translates to:
  /// **'Your name (optional)'**
  String get contactNameLabel;

  /// No description provided for @contactEmailLabel.
  ///
  /// In en, this message translates to:
  /// **'Email (optional, if you want a reply)'**
  String get contactEmailLabel;

  /// No description provided for @contactMessageLabel.
  ///
  /// In en, this message translates to:
  /// **'Message'**
  String get contactMessageLabel;

  /// No description provided for @contactMessageHint.
  ///
  /// In en, this message translates to:
  /// **'Corrections, tips, feedback…'**
  String get contactMessageHint;

  /// No description provided for @contactSend.
  ///
  /// In en, this message translates to:
  /// **'Send message'**
  String get contactSend;

  /// No description provided for @contactSent.
  ///
  /// In en, this message translates to:
  /// **'Message sent — thank you for writing to us.'**
  String get contactSent;

  /// No description provided for @contactTooShort.
  ///
  /// In en, this message translates to:
  /// **'Please write a few more words (at least 10 characters).'**
  String get contactTooShort;

  /// No description provided for @searchTitle.
  ///
  /// In en, this message translates to:
  /// **'Search'**
  String get searchTitle;

  /// No description provided for @searchSubtitle.
  ///
  /// In en, this message translates to:
  /// **'Search every article we have published.'**
  String get searchSubtitle;

  /// No description provided for @searchQueryLabel.
  ///
  /// In en, this message translates to:
  /// **'Search query'**
  String get searchQueryLabel;

  /// No description provided for @searchQueryHint.
  ///
  /// In en, this message translates to:
  /// **'Keywords, titles, summaries…'**
  String get searchQueryHint;

  /// No description provided for @searchAction.
  ///
  /// In en, this message translates to:
  /// **'Search'**
  String get searchAction;

  /// No description provided for @searchEngine.
  ///
  /// In en, this message translates to:
  /// **'Engine: {name}'**
  String searchEngine(String name);

  /// No description provided for @searchEmptyTitle.
  ///
  /// In en, this message translates to:
  /// **'No results'**
  String get searchEmptyTitle;

  /// No description provided for @searchEmptyMessage.
  ///
  /// In en, this message translates to:
  /// **'Try different keywords or check that articles are indexed.'**
  String get searchEmptyMessage;

  /// No description provided for @searchErrorBackend.
  ///
  /// In en, this message translates to:
  /// **'Search is temporarily unavailable. Check that the API and database are running.'**
  String get searchErrorBackend;

  /// No description provided for @suggestionsTitle.
  ///
  /// In en, this message translates to:
  /// **'Suggestions'**
  String get suggestionsTitle;

  /// No description provided for @suggestionsSubtitle.
  ///
  /// In en, this message translates to:
  /// **'Submit after sending at least 0.01 ALGO to the platform treasury. Upvotes use an off-chain wallet signature.'**
  String get suggestionsSubtitle;

  /// No description provided for @suggestionsNewTitle.
  ///
  /// In en, this message translates to:
  /// **'New suggestion'**
  String get suggestionsNewTitle;

  /// No description provided for @suggestionsFieldTitle.
  ///
  /// In en, this message translates to:
  /// **'Title'**
  String get suggestionsFieldTitle;

  /// No description provided for @suggestionsFieldBody.
  ///
  /// In en, this message translates to:
  /// **'Body'**
  String get suggestionsFieldBody;

  /// No description provided for @suggestionsFieldTxid.
  ///
  /// In en, this message translates to:
  /// **'Submission transaction ID'**
  String get suggestionsFieldTxid;

  /// No description provided for @suggestionsSubmit.
  ///
  /// In en, this message translates to:
  /// **'Submit suggestion'**
  String get suggestionsSubmit;

  /// No description provided for @suggestionsUpvoteTitle.
  ///
  /// In en, this message translates to:
  /// **'Upvote'**
  String get suggestionsUpvoteTitle;

  /// No description provided for @suggestionsSignatureLabel.
  ///
  /// In en, this message translates to:
  /// **'Signature (base64)'**
  String get suggestionsSignatureLabel;

  /// No description provided for @suggestionsSignatureHint.
  ///
  /// In en, this message translates to:
  /// **'After signing the prepared message in your wallet'**
  String get suggestionsSignatureHint;

  /// No description provided for @suggestionsSubmitUpvote.
  ///
  /// In en, this message translates to:
  /// **'Submit upvote'**
  String get suggestionsSubmitUpvote;

  /// No description provided for @suggestionsPrepareUpvote.
  ///
  /// In en, this message translates to:
  /// **'Prepare upvote'**
  String get suggestionsPrepareUpvote;

  /// No description provided for @suggestionsUpvoteDialogTitle.
  ///
  /// In en, this message translates to:
  /// **'Sign upvote message'**
  String get suggestionsUpvoteDialogTitle;

  /// No description provided for @suggestionsCopyMessage.
  ///
  /// In en, this message translates to:
  /// **'Copy message'**
  String get suggestionsCopyMessage;

  /// No description provided for @suggestionsMessageCopied.
  ///
  /// In en, this message translates to:
  /// **'Signing message copied to clipboard'**
  String get suggestionsMessageCopied;

  /// No description provided for @suggestionsTreasuryHelp.
  ///
  /// In en, this message translates to:
  /// **'Send at least {minAlgo} ALGO to the platform treasury before submitting:\n{address}'**
  String suggestionsTreasuryHelp(String minAlgo, String address);

  /// No description provided for @suggestionsUpvoteCount.
  ///
  /// In en, this message translates to:
  /// **'{count, plural, =0{No upvotes yet} =1{1 upvote} other{{count} upvotes}}'**
  String suggestionsUpvoteCount(int count);

  /// No description provided for @close.
  ///
  /// In en, this message translates to:
  /// **'Close'**
  String get close;

  /// No description provided for @suggestionsTxShort.
  ///
  /// In en, this message translates to:
  /// **'Tx {txid}'**
  String suggestionsTxShort(String txid);

  /// No description provided for @snackConnectWallet.
  ///
  /// In en, this message translates to:
  /// **'Connect your wallet first'**
  String get snackConnectWallet;

  /// No description provided for @snackSuggestionSubmitted.
  ///
  /// In en, this message translates to:
  /// **'Suggestion submitted'**
  String get snackSuggestionSubmitted;

  /// No description provided for @snackUpvoteRecorded.
  ///
  /// In en, this message translates to:
  /// **'Upvote recorded'**
  String get snackUpvoteRecorded;

  /// No description provided for @snackChooseSuggestionUpvote.
  ///
  /// In en, this message translates to:
  /// **'Choose a suggestion to upvote first'**
  String get snackChooseSuggestionUpvote;

  /// No description provided for @metaPublishedEpoch.
  ///
  /// In en, this message translates to:
  /// **'Published: {epoch}'**
  String metaPublishedEpoch(String epoch);

  /// No description provided for @metaPublishedRelative.
  ///
  /// In en, this message translates to:
  /// **'Published: {when}'**
  String metaPublishedRelative(String when);

  /// No description provided for @timeJustNow.
  ///
  /// In en, this message translates to:
  /// **'just now'**
  String get timeJustNow;

  /// No description provided for @timeMinutesAgo.
  ///
  /// In en, this message translates to:
  /// **'{count}m ago'**
  String timeMinutesAgo(int count);

  /// No description provided for @timeHoursAgo.
  ///
  /// In en, this message translates to:
  /// **'{count}h ago'**
  String timeHoursAgo(int count);

  /// No description provided for @timeDaysAgo.
  ///
  /// In en, this message translates to:
  /// **'{count}d ago'**
  String timeDaysAgo(int count);

  /// No description provided for @metaRound.
  ///
  /// In en, this message translates to:
  /// **'Round: {round}'**
  String metaRound(String round);

  /// No description provided for @metaService.
  ///
  /// In en, this message translates to:
  /// **'Service: {serviceId}'**
  String metaService(String serviceId);

  /// No description provided for @navFrontPage.
  ///
  /// In en, this message translates to:
  /// **'Front page'**
  String get navFrontPage;

  /// No description provided for @navLatest.
  ///
  /// In en, this message translates to:
  /// **'Latest'**
  String get navLatest;

  /// No description provided for @navSections.
  ///
  /// In en, this message translates to:
  /// **'SECTIONS'**
  String get navSections;

  /// No description provided for @navAbout.
  ///
  /// In en, this message translates to:
  /// **'About'**
  String get navAbout;

  /// No description provided for @navApps.
  ///
  /// In en, this message translates to:
  /// **'Apps'**
  String get navApps;

  /// No description provided for @navProductsMenuHint.
  ///
  /// In en, this message translates to:
  /// **'Explore the platform'**
  String get navProductsMenuHint;

  /// No description provided for @frontPageTopStories.
  ///
  /// In en, this message translates to:
  /// **'Top stories'**
  String get frontPageTopStories;

  /// No description provided for @frontPageLatest.
  ///
  /// In en, this message translates to:
  /// **'Latest'**
  String get frontPageLatest;

  /// No description provided for @frontPageMore.
  ///
  /// In en, this message translates to:
  /// **'More from the newsroom'**
  String get frontPageMore;

  /// No description provided for @frontPageSectionStories.
  ///
  /// In en, this message translates to:
  /// **'More in {section}'**
  String frontPageSectionStories(String section);

  /// No description provided for @navHot.
  ///
  /// In en, this message translates to:
  /// **'Hot'**
  String get navHot;

  /// No description provided for @navTopics.
  ///
  /// In en, this message translates to:
  /// **'Topics'**
  String get navTopics;

  /// No description provided for @hotTitle.
  ///
  /// In en, this message translates to:
  /// **'Most read'**
  String get hotTitle;

  /// No description provided for @hotLead.
  ///
  /// In en, this message translates to:
  /// **'The stories readers are opening most right now.'**
  String get hotLead;

  /// No description provided for @topicsTitle.
  ///
  /// In en, this message translates to:
  /// **'Topics'**
  String get topicsTitle;

  /// No description provided for @topicsLead.
  ///
  /// In en, this message translates to:
  /// **'Every tag the newsroom used recently — sized by coverage, warmed by reads.'**
  String get topicsLead;

  /// No description provided for @topicSubtitle.
  ///
  /// In en, this message translates to:
  /// **'Stories tagged “{tag}”'**
  String topicSubtitle(String tag);

  /// No description provided for @readsCount.
  ///
  /// In en, this message translates to:
  /// **'{count, plural, =1{1 read} other{{count} reads}}'**
  String readsCount(int count);

  /// No description provided for @sectionMarkets.
  ///
  /// In en, this message translates to:
  /// **'Markets'**
  String get sectionMarkets;

  /// No description provided for @sectionSecurity.
  ///
  /// In en, this message translates to:
  /// **'Security'**
  String get sectionSecurity;

  /// No description provided for @sectionDevelopers.
  ///
  /// In en, this message translates to:
  /// **'Developers'**
  String get sectionDevelopers;

  /// No description provided for @sectionCommunity.
  ///
  /// In en, this message translates to:
  /// **'Community'**
  String get sectionCommunity;

  /// No description provided for @sectionEcosystem.
  ///
  /// In en, this message translates to:
  /// **'Ecosystem'**
  String get sectionEcosystem;

  /// No description provided for @sectionEmptyTitle.
  ///
  /// In en, this message translates to:
  /// **'Nothing here yet'**
  String get sectionEmptyTitle;

  /// No description provided for @sectionEmptyMessage.
  ///
  /// In en, this message translates to:
  /// **'No stories have been filed in this section. Check back soon.'**
  String get sectionEmptyMessage;

  /// No description provided for @bylineNewsroom.
  ///
  /// In en, this message translates to:
  /// **'The Newsroom'**
  String get bylineNewsroom;

  /// No description provided for @bylineMarketsDesk.
  ///
  /// In en, this message translates to:
  /// **'Markets Desk'**
  String get bylineMarketsDesk;

  /// No description provided for @bylineChainDesk.
  ///
  /// In en, this message translates to:
  /// **'On-Chain Desk'**
  String get bylineChainDesk;

  /// No description provided for @articleByline.
  ///
  /// In en, this message translates to:
  /// **'By {desk}'**
  String articleByline(String desk);

  /// No description provided for @articleReadingTime.
  ///
  /// In en, this message translates to:
  /// **'{count} min read'**
  String articleReadingTime(int count);

  /// No description provided for @articleRelatedTitle.
  ///
  /// In en, this message translates to:
  /// **'Related stories'**
  String get articleRelatedTitle;

  /// No description provided for @articleShare.
  ///
  /// In en, this message translates to:
  /// **'Share'**
  String get articleShare;

  /// No description provided for @articleShareCopyLink.
  ///
  /// In en, this message translates to:
  /// **'Copy link'**
  String get articleShareCopyLink;

  /// No description provided for @articleLinkCopied.
  ///
  /// In en, this message translates to:
  /// **'Link copied to clipboard'**
  String get articleLinkCopied;

  /// No description provided for @footerTagline.
  ///
  /// In en, this message translates to:
  /// **'Independent, automated coverage of the Algorand ecosystem.'**
  String get footerTagline;

  /// No description provided for @footerSectionsHeading.
  ///
  /// In en, this message translates to:
  /// **'Sections'**
  String get footerSectionsHeading;

  /// No description provided for @footerAboutHeading.
  ///
  /// In en, this message translates to:
  /// **'About'**
  String get footerAboutHeading;

  /// No description provided for @footerFollowHeading.
  ///
  /// In en, this message translates to:
  /// **'Follow'**
  String get footerFollowHeading;

  /// No description provided for @footerRights.
  ///
  /// In en, this message translates to:
  /// **'© {year} PXke Algorand. Independent coverage of the Algorand ecosystem.'**
  String footerRights(String year);

  /// No description provided for @aboutTitle.
  ///
  /// In en, this message translates to:
  /// **'About PXke Algorand'**
  String get aboutTitle;

  /// No description provided for @aboutLead.
  ///
  /// In en, this message translates to:
  /// **'PXke Algorand is an independent newsroom covering the Algorand blockchain — its markets, protocol, governance, and the projects building on it.'**
  String get aboutLead;

  /// No description provided for @aboutHowHeading.
  ///
  /// In en, this message translates to:
  /// **'How we publish'**
  String get aboutHowHeading;

  /// No description provided for @aboutHowBody.
  ///
  /// In en, this message translates to:
  /// **'Our coverage is assembled automatically from on-chain events, scheduled market data, and monitored community sources, then organised into sections by our editorial pipeline. Every story carries a provenance line so you can trace where it came from.'**
  String get aboutHowBody;

  /// No description provided for @aboutAiHeading.
  ///
  /// In en, this message translates to:
  /// **'Written with AI'**
  String get aboutAiHeading;

  /// No description provided for @aboutAiBody.
  ///
  /// In en, this message translates to:
  /// **'PXke Algorand publishes AI-assisted journalism. Our articles are drafted by AI language models from the on-chain events, market data, and community sources above, under automated editorial review — they are machine-generated rather than written by human reporters. We link to the original sources on every story so you can verify each claim, and the organisation, not an individual byline, is the author of record.'**
  String get aboutAiBody;

  /// No description provided for @aboutProvenanceHeading.
  ///
  /// In en, this message translates to:
  /// **'Provenance & transparency'**
  String get aboutProvenanceHeading;

  /// No description provided for @aboutProvenanceBody.
  ///
  /// In en, this message translates to:
  /// **'On-chain stories are triggered by verifiable transactions and link back to the Algorand explorer. Market stories draw on CoinGecko data. Sponsored placements are always labelled as such and are kept clearly separate from editorial.'**
  String get aboutProvenanceBody;

  /// No description provided for @aboutStandardsHeading.
  ///
  /// In en, this message translates to:
  /// **'Editorial standards'**
  String get aboutStandardsHeading;

  /// No description provided for @aboutStandardsBody.
  ///
  /// In en, this message translates to:
  /// **'We aim for accuracy and clear attribution. This publication is not investment advice. Spotted an error? Reach out through the source links on any story.'**
  String get aboutStandardsBody;

  /// No description provided for @seedsTitle.
  ///
  /// In en, this message translates to:
  /// **'Seeds'**
  String get seedsTitle;

  /// No description provided for @seedsSubtitle.
  ///
  /// In en, this message translates to:
  /// **'Manually-configured starting points for the crawler. Domains approved from discovery live in the Domains tab.'**
  String get seedsSubtitle;

  /// No description provided for @sourcesAdd.
  ///
  /// In en, this message translates to:
  /// **'Add source'**
  String get sourcesAdd;

  /// No description provided for @sourcesEdit.
  ///
  /// In en, this message translates to:
  /// **'Edit'**
  String get sourcesEdit;

  /// No description provided for @sourcesEditTitle.
  ///
  /// In en, this message translates to:
  /// **'Edit source'**
  String get sourcesEditTitle;

  /// No description provided for @sourcesAddTitle.
  ///
  /// In en, this message translates to:
  /// **'Add source'**
  String get sourcesAddTitle;

  /// No description provided for @sourcesDeleteTitle.
  ///
  /// In en, this message translates to:
  /// **'Delete source?'**
  String get sourcesDeleteTitle;

  /// No description provided for @sourcesDeleteBody.
  ///
  /// In en, this message translates to:
  /// **'\"{serviceId}\" will stop being crawled. Articles already published stay.'**
  String sourcesDeleteBody(String serviceId);

  /// No description provided for @sourcesDelete.
  ///
  /// In en, this message translates to:
  /// **'Delete'**
  String get sourcesDelete;

  /// No description provided for @sourcesSave.
  ///
  /// In en, this message translates to:
  /// **'Save'**
  String get sourcesSave;

  /// No description provided for @sourcesAddAction.
  ///
  /// In en, this message translates to:
  /// **'Add'**
  String get sourcesAddAction;

  /// No description provided for @sourcesRequiredFields.
  ///
  /// In en, this message translates to:
  /// **'Service id, name and URL are required.'**
  String get sourcesRequiredFields;

  /// No description provided for @sourcesChangesNextPoll.
  ///
  /// In en, this message translates to:
  /// **'Changes apply on the next crawler poll.'**
  String get sourcesChangesNextPoll;

  /// No description provided for @sourcesMerge.
  ///
  /// In en, this message translates to:
  /// **'Merge'**
  String get sourcesMerge;

  /// No description provided for @sourcesMergeTitle.
  ///
  /// In en, this message translates to:
  /// **'Merge services'**
  String get sourcesMergeTitle;

  /// No description provided for @sourcesMergeIntro.
  ///
  /// In en, this message translates to:
  /// **'Fold several services into one. The chosen services\' sources and domains move to the target; the emptied services are disabled. Use this when one product spans multiple domains (e.g. algorand.co + algorand.com).'**
  String get sourcesMergeIntro;

  /// No description provided for @sourcesMergeTarget.
  ///
  /// In en, this message translates to:
  /// **'Keep as target'**
  String get sourcesMergeTarget;

  /// No description provided for @sourcesMergeFold.
  ///
  /// In en, this message translates to:
  /// **'Fold in (will be disabled)'**
  String get sourcesMergeFold;

  /// No description provided for @sourcesMergeAction.
  ///
  /// In en, this message translates to:
  /// **'Merge'**
  String get sourcesMergeAction;

  /// No description provided for @sourcesMergeNeedsTwo.
  ///
  /// In en, this message translates to:
  /// **'Pick a target and at least one service to fold in.'**
  String get sourcesMergeNeedsTwo;

  /// No description provided for @sourcesMergeDone.
  ///
  /// In en, this message translates to:
  /// **'Merged {count} service(s) into {target}.'**
  String sourcesMergeDone(int count, String target);

  /// No description provided for @sourcesFieldServiceId.
  ///
  /// In en, this message translates to:
  /// **'Service id'**
  String get sourcesFieldServiceId;

  /// No description provided for @sourcesFieldServiceIdHint.
  ///
  /// In en, this message translates to:
  /// **'kebab-case, e.g. algorand-foundation-blog'**
  String get sourcesFieldServiceIdHint;

  /// No description provided for @sourcesFieldDisplayName.
  ///
  /// In en, this message translates to:
  /// **'Display name'**
  String get sourcesFieldDisplayName;

  /// No description provided for @sourcesFieldScrapeUrl.
  ///
  /// In en, this message translates to:
  /// **'Scrape URL'**
  String get sourcesFieldScrapeUrl;

  /// No description provided for @sourcesFieldScrapeUrlHint.
  ///
  /// In en, this message translates to:
  /// **'https://…, reddit://r/…, discord://channel/…'**
  String get sourcesFieldScrapeUrlHint;

  /// No description provided for @sourcesFieldMatchKind.
  ///
  /// In en, this message translates to:
  /// **'Match kind'**
  String get sourcesFieldMatchKind;

  /// No description provided for @sourcesFieldMatchKindHint.
  ///
  /// In en, this message translates to:
  /// **'address / app_id / asset_id'**
  String get sourcesFieldMatchKindHint;

  /// No description provided for @sourcesFieldMatchValue.
  ///
  /// In en, this message translates to:
  /// **'Match value'**
  String get sourcesFieldMatchValue;

  /// No description provided for @sourcesFieldMatchValueHint.
  ///
  /// In en, this message translates to:
  /// **'e.g. wallet address or app id'**
  String get sourcesFieldMatchValueHint;

  /// No description provided for @sourcesMatchRuleHelp.
  ///
  /// In en, this message translates to:
  /// **'The match rule links this source to on-chain activity: when the chain crawler sees a MainNet transaction whose sender/receiver address (kind \"address\"), application id (\"app_id\") or asset id (\"asset_id\") equals the match value, it attributes the event to this source and can trigger an article. For web or Reddit sources it is purely informational — use something descriptive like the domain or subreddit.'**
  String get sourcesMatchRuleHelp;

  /// No description provided for @sourcesEnabled.
  ///
  /// In en, this message translates to:
  /// **'Enabled'**
  String get sourcesEnabled;

  /// No description provided for @actionCancel.
  ///
  /// In en, this message translates to:
  /// **'Cancel'**
  String get actionCancel;

  /// No description provided for @actionRefresh.
  ///
  /// In en, this message translates to:
  /// **'Refresh'**
  String get actionRefresh;

  /// No description provided for @adminTabSeeds.
  ///
  /// In en, this message translates to:
  /// **'Seeds'**
  String get adminTabSeeds;

  /// No description provided for @adminTabArticles.
  ///
  /// In en, this message translates to:
  /// **'Articles'**
  String get adminTabArticles;

  /// No description provided for @adminTabWriterBriefs.
  ///
  /// In en, this message translates to:
  /// **'Writer briefs'**
  String get adminTabWriterBriefs;

  /// No description provided for @adminTabClassifier.
  ///
  /// In en, this message translates to:
  /// **'Classifier'**
  String get adminTabClassifier;

  /// No description provided for @adminTabDomains.
  ///
  /// In en, this message translates to:
  /// **'Domains'**
  String get adminTabDomains;

  /// No description provided for @adminTabToolInsights.
  ///
  /// In en, this message translates to:
  /// **'Tool insights'**
  String get adminTabToolInsights;

  /// No description provided for @adminTabSessions.
  ///
  /// In en, this message translates to:
  /// **'Sessions'**
  String get adminTabSessions;

  /// No description provided for @adminTabSystem.
  ///
  /// In en, this message translates to:
  /// **'System'**
  String get adminTabSystem;

  /// No description provided for @domainsIntro.
  ///
  /// In en, this message translates to:
  /// **'Crawl frontier: domains the link crawler has met. Dead ends are never explored — set automatically by relevance scoring and your review verdicts, or manually here.'**
  String get domainsIntro;

  /// No description provided for @domainsFilterAll.
  ///
  /// In en, this message translates to:
  /// **'All'**
  String get domainsFilterAll;

  /// No description provided for @domainsFilterAllCount.
  ///
  /// In en, this message translates to:
  /// **'All ({count})'**
  String domainsFilterAllCount(int count);

  /// No description provided for @domainsFilterPending.
  ///
  /// In en, this message translates to:
  /// **'Pending review'**
  String get domainsFilterPending;

  /// No description provided for @domainsFilterPendingCount.
  ///
  /// In en, this message translates to:
  /// **'Pending review ({count})'**
  String domainsFilterPendingCount(int count);

  /// No description provided for @domainsFilterDeadEnds.
  ///
  /// In en, this message translates to:
  /// **'Dead ends'**
  String get domainsFilterDeadEnds;

  /// No description provided for @domainsFilterDeadEndsCount.
  ///
  /// In en, this message translates to:
  /// **'Dead ends ({count})'**
  String domainsFilterDeadEndsCount(int count);

  /// No description provided for @domainsEmptyTitle.
  ///
  /// In en, this message translates to:
  /// **'No domains yet'**
  String get domainsEmptyTitle;

  /// No description provided for @domainsEmptyMessage.
  ///
  /// In en, this message translates to:
  /// **'The crawler records every domain it meets while following links.'**
  String get domainsEmptyMessage;

  /// No description provided for @domainsOpenInNewTab.
  ///
  /// In en, this message translates to:
  /// **'Open {domain} in a new tab'**
  String domainsOpenInNewTab(String domain);

  /// No description provided for @domainsKeywords.
  ///
  /// In en, this message translates to:
  /// **'keywords: {keywords}'**
  String domainsKeywords(String keywords);

  /// No description provided for @domainsLinkedAs.
  ///
  /// In en, this message translates to:
  /// **'linked as \"{text}\"'**
  String domainsLinkedAs(String text);

  /// No description provided for @domainsPredictedInterest.
  ///
  /// In en, this message translates to:
  /// **'predicted interest {score}'**
  String domainsPredictedInterest(String score);

  /// No description provided for @domainsFoundOn.
  ///
  /// In en, this message translates to:
  /// **'found on {url}'**
  String domainsFoundOn(String url);

  /// No description provided for @domainsScore.
  ///
  /// In en, this message translates to:
  /// **'score {score}'**
  String domainsScore(String score);

  /// No description provided for @domainsCrawled.
  ///
  /// In en, this message translates to:
  /// **'crawled {date}'**
  String domainsCrawled(String date);

  /// No description provided for @domainsPagesCrawled.
  ///
  /// In en, this message translates to:
  /// **'{count} pages'**
  String domainsPagesCrawled(int count);

  /// No description provided for @domainsDeadEnd.
  ///
  /// In en, this message translates to:
  /// **'Dead end'**
  String get domainsDeadEnd;

  /// No description provided for @domainsApproveExplore.
  ///
  /// In en, this message translates to:
  /// **'Approve & explore'**
  String get domainsApproveExplore;

  /// No description provided for @domainsCrawlOnce.
  ///
  /// In en, this message translates to:
  /// **'Crawl once, no seed'**
  String get domainsCrawlOnce;

  /// No description provided for @domainsAddButton.
  ///
  /// In en, this message translates to:
  /// **'Add'**
  String get domainsAddButton;

  /// No description provided for @domainsAddAsSeed.
  ///
  /// In en, this message translates to:
  /// **'Add as permanent source'**
  String get domainsAddAsSeed;

  /// No description provided for @domainsScoreUnexplained.
  ///
  /// In en, this message translates to:
  /// **'Score breakdown not available'**
  String get domainsScoreUnexplained;

  /// No description provided for @paginationPrevious.
  ///
  /// In en, this message translates to:
  /// **'Previous'**
  String get paginationPrevious;

  /// No description provided for @paginationNext.
  ///
  /// In en, this message translates to:
  /// **'Next'**
  String get paginationNext;

  /// No description provided for @paginationPageOf.
  ///
  /// In en, this message translates to:
  /// **'Page {page} of {total}'**
  String paginationPageOf(int page, int total);

  /// No description provided for @domainsMarkDeadEnd.
  ///
  /// In en, this message translates to:
  /// **'Mark dead end'**
  String get domainsMarkDeadEnd;

  /// No description provided for @domainsRevive.
  ///
  /// In en, this message translates to:
  /// **'Revive'**
  String get domainsRevive;

  /// No description provided for @domainsApprovedSnack.
  ///
  /// In en, this message translates to:
  /// **'{domain} approved — the crawler may explore it'**
  String domainsApprovedSnack(String domain);

  /// No description provided for @domainsDeadEndSnack.
  ///
  /// In en, this message translates to:
  /// **'{domain} marked as dead end'**
  String domainsDeadEndSnack(String domain);

  /// No description provided for @domainsWalletNotConnected.
  ///
  /// In en, this message translates to:
  /// **'Wallet not connected'**
  String get domainsWalletNotConnected;

  /// No description provided for @frontPageMoreNews.
  ///
  /// In en, this message translates to:
  /// **'More news'**
  String get frontPageMoreNews;

  /// No description provided for @storiesCount.
  ///
  /// In en, this message translates to:
  /// **'{count, plural, =1{1 story} other{{count} stories}}'**
  String storiesCount(int count);

  /// No description provided for @byTheNumbersRange.
  ///
  /// In en, this message translates to:
  /// **'Past 7 days'**
  String get byTheNumbersRange;

  /// No description provided for @byTheNumbersMarketCap.
  ///
  /// In en, this message translates to:
  /// **'Market cap'**
  String get byTheNumbersMarketCap;

  /// No description provided for @byTheNumbersVolume.
  ///
  /// In en, this message translates to:
  /// **'24h volume'**
  String get byTheNumbersVolume;

  /// No description provided for @hotTabHot.
  ///
  /// In en, this message translates to:
  /// **'Hot right now'**
  String get hotTabHot;

  /// No description provided for @hotTabAllTime.
  ///
  /// In en, this message translates to:
  /// **'All-time'**
  String get hotTabAllTime;
}

class _AppLocalizationsDelegate
    extends LocalizationsDelegate<AppLocalizations> {
  const _AppLocalizationsDelegate();

  @override
  Future<AppLocalizations> load(Locale locale) {
    return lookupAppLocalizations(locale);
  }

  @override
  bool isSupported(Locale locale) => <String>[
    'ar',
    'en',
    'es',
    'fa',
    'fr',
    'hi',
    'ps',
    'ru',
    'zh',
  ].contains(locale.languageCode);

  @override
  bool shouldReload(_AppLocalizationsDelegate old) => false;
}

Future<AppLocalizations> lookupAppLocalizations(Locale locale) {
  // Lookup logic when only language code is specified.
  switch (locale.languageCode) {
    case 'ar':
      return app_localizations_ar.loadLibrary().then(
        (dynamic _) => app_localizations_ar.AppLocalizationsAr(),
      );
    case 'en':
      return app_localizations_en.loadLibrary().then(
        (dynamic _) => app_localizations_en.AppLocalizationsEn(),
      );
    case 'es':
      return app_localizations_es.loadLibrary().then(
        (dynamic _) => app_localizations_es.AppLocalizationsEs(),
      );
    case 'fa':
      return app_localizations_fa.loadLibrary().then(
        (dynamic _) => app_localizations_fa.AppLocalizationsFa(),
      );
    case 'fr':
      return app_localizations_fr.loadLibrary().then(
        (dynamic _) => app_localizations_fr.AppLocalizationsFr(),
      );
    case 'hi':
      return app_localizations_hi.loadLibrary().then(
        (dynamic _) => app_localizations_hi.AppLocalizationsHi(),
      );
    case 'ps':
      return app_localizations_ps.loadLibrary().then(
        (dynamic _) => app_localizations_ps.AppLocalizationsPs(),
      );
    case 'ru':
      return app_localizations_ru.loadLibrary().then(
        (dynamic _) => app_localizations_ru.AppLocalizationsRu(),
      );
    case 'zh':
      return app_localizations_zh.loadLibrary().then(
        (dynamic _) => app_localizations_zh.AppLocalizationsZh(),
      );
  }

  throw FlutterError(
    'AppLocalizations.delegate failed to load unsupported locale "$locale". This is likely '
    'an issue with the localizations generation tool. Please file an issue '
    'on GitHub with a reproducible sample app and the gen-l10n configuration '
    'that was used.',
  );
}
