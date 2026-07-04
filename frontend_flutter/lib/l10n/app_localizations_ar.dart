// ignore: unused_import
import 'package:intl/intl.dart' as intl;
import 'app_localizations.dart';

// ignore_for_file: type=lint

/// The translations for Arabic (`ar`).
class AppLocalizationsAr extends AppLocalizations {
  AppLocalizationsAr([String locale = 'ar']) : super(locale);

  @override
  String get appTitle => 'PXke Algorand Projects';

  @override
  String get appTagline => 'أخبار ومصادر ومجتمع';

  @override
  String get navHome => 'الرئيسية';

  @override
  String get navNews => 'الأخبار';

  @override
  String get navSources => 'Sources';

  @override
  String get navSuggestions => 'اقتراحات';

  @override
  String get navSearch => 'بحث';

  @override
  String get navAdmin => 'الإدارة';

  @override
  String get navProducts => 'المنتجات';

  @override
  String get navWallet => 'Wallet';

  @override
  String get navAppearance => 'APPEARANCE';

  @override
  String get pageTitleHome => 'PXke Algorand Projects';

  @override
  String get pageTitleArticle => 'Article';

  @override
  String get pageTitleNews => 'الأخبار';

  @override
  String get pageTitleSources => 'News sources';

  @override
  String get pageTitleSuggestions => 'اقتراحات';

  @override
  String get pageTitleSearch => 'بحث';

  @override
  String get pageTitleAdmin => 'الإدارة';

  @override
  String get backToFeed => 'العودة إلى الخلاصة';

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
  String get homeOpenProduct => 'فتح';

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
  String get localeSystem => 'لغة النظام';

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
  String get localeHindi => 'الهندية';

  @override
  String get navLanguage => 'اللغة';

  @override
  String get walletConnected => 'متصل';

  @override
  String get walletDisconnect => 'قطع الاتصال';

  @override
  String get walletSignInTitle => 'Sign in with wallet';

  @override
  String get walletSignInBody =>
      'Connect a WalletConnect-compatible Algorand wallet to submit suggestions and upvotes.';

  @override
  String get walletConnect => 'ربط المحفظة';

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
      'تم اتصال المحفظة. ارجع إلى تطبيق محفظتك للموافقة على طلب تسجيل الدخول ثم عُد إلى هنا.';

  @override
  String get walletAwaitingApprovalTitle => 'أوشكت على الانتهاء';

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
  String get newsFeedTitle => 'أحدث المقالات';

  @override
  String get newsSubtitleDefault =>
      'On-chain triggers and crawled sources publish here when content changes.';

  @override
  String get articlePublicationDetailsHint => 'الناشر والتاريخ ورابط المصدر';

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
  String get articleViewOnExplorer => 'عرض على مستكشف Algorand';

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
      'اربط محفظة مسؤول. عيّن ADMIN_WALLET_ADDRESSES عند البناء.';

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
  String get navContact => 'اتصل بنا';

  @override
  String get contactTitle => 'اتصل بنا';

  @override
  String get contactSubtitle =>
      'تصحيحات أو معلومات أو ملاحظات — راسل هيئة التحرير.';

  @override
  String get contactNameLabel => 'اسمك (اختياري)';

  @override
  String get contactEmailLabel => 'البريد الإلكتروني (اختياري، إذا أردت ردًا)';

  @override
  String get contactMessageLabel => 'الرسالة';

  @override
  String get contactMessageHint => 'تصحيحات، معلومات، ملاحظات…';

  @override
  String get contactSend => 'إرسال الرسالة';

  @override
  String get contactSent => 'تم إرسال الرسالة — شكرًا لمراسلتنا.';

  @override
  String get contactTooShort => 'يرجى كتابة المزيد (10 أحرف على الأقل).';

  @override
  String get searchTitle => 'بحث';

  @override
  String get searchSubtitle => 'ابحث في جميع مقالاتنا المنشورة.';

  @override
  String get searchQueryLabel => 'استعلام البحث';

  @override
  String get searchQueryHint => 'كلمات مفتاحية، عناوين، ملخصات…';

  @override
  String get searchAction => 'بحث';

  @override
  String searchEngine(String name) {
    return 'Engine: $name';
  }

  @override
  String get searchEmptyTitle => 'لا نتائج';

  @override
  String get searchEmptyMessage =>
      'Try different keywords or check that articles are indexed.';

  @override
  String get searchErrorBackend =>
      'البحث غير متاح مؤقتًا. تأكد من تشغيل واجهة API وقاعدة البيانات.';

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
    return 'الخدمة: $serviceId';
  }

  @override
  String get navFrontPage => 'الصفحة الأولى';

  @override
  String get navLatest => 'الأحدث';

  @override
  String get navSections => 'الأقسام';

  @override
  String get navAbout => 'حول';

  @override
  String get navApps => 'التطبيقات';

  @override
  String get navProductsMenuHint => 'استكشف المنصة';

  @override
  String get frontPageTopStories => 'أهم القصص';

  @override
  String get frontPageLatest => 'الأحدث';

  @override
  String get frontPageMore => 'المزيد من غرفة الأخبار';

  @override
  String frontPageSectionStories(String section) {
    return 'المزيد في $section';
  }

  @override
  String get sectionMarkets => 'الأسواق';

  @override
  String get sectionSecurity => 'الأمن';

  @override
  String get sectionDevelopers => 'المطورون';

  @override
  String get sectionCommunity => 'المجتمع';

  @override
  String get sectionEcosystem => 'النظام البيئي';

  @override
  String get sectionEmptyTitle => 'لا شيء هنا بعد';

  @override
  String get sectionEmptyMessage => 'لا توجد قصص في هذا القسم بعد. عد قريباً.';

  @override
  String get bylineNewsroom => 'غرفة الأخبار';

  @override
  String get bylineMarketsDesk => 'مكتب الأسواق';

  @override
  String get bylineChainDesk => 'مكتب على السلسلة';

  @override
  String articleByline(String desk) {
    return 'بقلم $desk';
  }

  @override
  String articleReadingTime(int count) {
    return '$count دقائق قراءة';
  }

  @override
  String get articleRelatedTitle => 'قصص ذات صلة';

  @override
  String get articleShare => 'مشاركة';

  @override
  String get articleLinkCopied => 'تم نسخ الرابط';

  @override
  String get footerTagline => 'تغطية مستقلة وآلية لنظام Algorand البيئي.';

  @override
  String get footerSectionsHeading => 'الأقسام';

  @override
  String get footerAboutHeading => 'حول';

  @override
  String footerRights(String year) {
    return '© $year PXke Algorand. تغطية مستقلة لنظام Algorand البيئي.';
  }

  @override
  String get aboutTitle => 'حول PXke Algorand';

  @override
  String get aboutLead =>
      'PXke Algorand هي غرفة أخبار مستقلة تغطي بلوكتشين Algorand — أسواقها وبروتوكولها وحوكمتها والمشاريع المبنية عليها.';

  @override
  String get aboutHowHeading => 'كيف ننشر';

  @override
  String get aboutHowBody =>
      'نُجمّع التغطية تلقائياً من أحداث على السلسلة وبيانات السوق والمصادر المجتمعية المراقبة، ثم ننظمها في أقسام. كل قصة تتضمن مصدراً يمكنك تتبعه.';

  @override
  String get aboutAiHeading => 'مكتوب بالذكاء الاصطناعي';

  @override
  String get aboutAiBody =>
      'ينشر PXke Algorand صحافة بمساعدة الذكاء الاصطناعي. تُصاغ المقالات بنماذج لغوية من أحداث على السلسلة وبيانات السوق والمصادر المجتمعية، تحت مراجعة تحريرية آلية.';

  @override
  String get aboutProvenanceHeading => 'المصدر والشفافية';

  @override
  String get aboutProvenanceBody =>
      'تُفعَّل قصص على السلسلة بمعاملات قابلة للتحقق وترتبط بمستكشف Algorand. قصص السوق من CoinGecko. المحتوى المموَّل يُوسَّم دائماً.';

  @override
  String get aboutStandardsHeading => 'المعايير التحريرية';

  @override
  String get aboutStandardsBody =>
      'نسعى للدقة والإسناد الواضح. هذا ليس نصيحة استثمارية. لاحظت خطأً؟ تواصل عبر روابط المصدر في أي قصة.';

  @override
  String get seedsTitle => 'البذور';

  @override
  String get seedsSubtitle =>
      'نقاط انطلاق يُعدّها المشرف يدويًا للزاحف. النطاقات المعتمدة من الاكتشاف تظهر في تبويب النطاقات.';

  @override
  String get sourcesAdd => 'إضافة مصدر';

  @override
  String get sourcesEdit => 'تعديل';

  @override
  String get sourcesEditTitle => 'تعديل المصدر';

  @override
  String get sourcesAddTitle => 'إضافة مصدر';

  @override
  String get sourcesDeleteTitle => 'حذف المصدر؟';

  @override
  String sourcesDeleteBody(String serviceId) {
    return 'سيتوقف الزحف إلى «$serviceId». المقالات المنشورة سابقًا تبقى كما هي.';
  }

  @override
  String get sourcesDelete => 'حذف';

  @override
  String get sourcesSave => 'حفظ';

  @override
  String get sourcesAddAction => 'إضافة';

  @override
  String get sourcesRequiredFields => 'معرّف الخدمة والاسم والرابط مطلوبة.';

  @override
  String get sourcesChangesNextPoll =>
      'تُطبَّق التغييرات في الدورة التالية للزاحف.';

  @override
  String get sourcesMerge => 'دمج';

  @override
  String get sourcesMergeTitle => 'دمج الخدمات';

  @override
  String get sourcesMergeIntro =>
      'ادمج عدة خدمات في واحدة. تنتقل مصادر ونطاقات الخدمات المختارة إلى الهدف؛ وتُعطَّل الفارغة.';

  @override
  String get sourcesMergeTarget => 'الإبقاء كهدف';

  @override
  String get sourcesMergeFold => 'طي (سيُعطَّل)';

  @override
  String get sourcesMergeAction => 'دمج';

  @override
  String get sourcesMergeNeedsTwo => 'اختر هدفاً وخدمة واحدة على الأقل للطي.';

  @override
  String sourcesMergeDone(int count, String target) {
    return 'تم دمج $count خدمة في $target.';
  }

  @override
  String get sourcesFieldServiceId => 'معرّف الخدمة';

  @override
  String get sourcesFieldServiceIdHint =>
      'kebab-case، مثل algorand-foundation-blog';

  @override
  String get sourcesFieldDisplayName => 'الاسم المعروض';

  @override
  String get sourcesFieldScrapeUrl => 'رابط الزحف';

  @override
  String get sourcesFieldScrapeUrlHint =>
      'https://… أو reddit://r/… أو discord://channel/…';

  @override
  String get sourcesFieldMatchKind => 'نوع المطابقة';

  @override
  String get sourcesFieldMatchKindHint => 'address / app_id / asset_id';

  @override
  String get sourcesFieldMatchValue => 'قيمة المطابقة';

  @override
  String get sourcesFieldMatchValueHint => 'مثل عنوان المحفظة أو معرّف التطبيق';

  @override
  String get sourcesMatchRuleHelp =>
      'قاعدة المطابقة تربط هذا المصدر بنشاط السلسلة: عندما يرى زاحف السلسلة معاملة على MainNet يتطابق فيها عنوان المرسل/المستلم (نوع «address») أو معرّف التطبيق («app_id») أو معرّف الأصل («asset_id») مع القيمة، يُنسب الحدث إلى هذا المصدر وقد يُنشئ مقالًا. لمصادر الويب أو Reddit فهي للمعلومات فقط — استخدم اسم النطاق أو subreddit مثلًا.';

  @override
  String get sourcesEnabled => 'مفعّل';

  @override
  String get actionCancel => 'إلغاء';

  @override
  String get actionRefresh => 'تحديث';

  @override
  String get adminTabSeeds => 'البذور';

  @override
  String get adminTabArticles => 'المقالات';

  @override
  String get adminTabWriterBriefs => 'ملخصات الكاتب';

  @override
  String get adminTabClassifier => 'المصنّف';

  @override
  String get adminTabDomains => 'النطاقات';

  @override
  String get adminTabToolInsights => 'أدوات البحث';

  @override
  String get adminTabSessions => 'الجلسات';

  @override
  String get adminTabSystem => 'النظام';

  @override
  String get domainsIntro =>
      'حدود الزحف: النطاقات التي التقى بها زاحف الروابط. المسارات المسدودة لا تُستكشف أبدًا — تُحدَّد تلقائيًا بالأهمية وحكمك اليدوي هنا.';

  @override
  String get domainsFilterAll => 'الكل';

  @override
  String domainsFilterAllCount(int count) {
    return 'الكل ($count)';
  }

  @override
  String get domainsFilterPending => 'بانتظار المراجعة';

  @override
  String domainsFilterPendingCount(int count) {
    return 'بانتظار المراجعة ($count)';
  }

  @override
  String get domainsFilterDeadEnds => 'مسارات مسدودة';

  @override
  String domainsFilterDeadEndsCount(int count) {
    return 'مسارات مسدودة ($count)';
  }

  @override
  String get domainsEmptyTitle => 'لا توجد نطاقات بعد';

  @override
  String get domainsEmptyMessage =>
      'يسجّل الزاحف كل نطاق يجده أثناء متابعة الروابط.';

  @override
  String domainsOpenInNewTab(String domain) {
    return 'فتح $domain في تبويب جديد';
  }

  @override
  String domainsKeywords(String keywords) {
    return 'كلمات مفتاحية: $keywords';
  }

  @override
  String domainsLinkedAs(String text) {
    return 'مرتبط كـ «$text»';
  }

  @override
  String domainsPredictedInterest(String score) {
    return 'الاهتمام المتوقع $score';
  }

  @override
  String domainsFoundOn(String url) {
    return 'وُجد على $url';
  }

  @override
  String domainsScore(String score) {
    return 'الدرجة $score';
  }

  @override
  String domainsCrawled(String date) {
    return 'زُحف في $date';
  }

  @override
  String domainsPagesCrawled(int count) {
    return '$count صفحة';
  }

  @override
  String get domainsDeadEnd => 'مسار مسدود';

  @override
  String get domainsApproveExplore => 'اعتماد واستكشاف';

  @override
  String get domainsMarkDeadEnd => 'تعليم كمسار مسدود';

  @override
  String get domainsRevive => 'إعادة تفعيل';

  @override
  String domainsApprovedSnack(String domain) {
    return 'تم اعتماد $domain — يمكن للزاحف استكشافه';
  }

  @override
  String domainsDeadEndSnack(String domain) {
    return 'تم تعليم $domain كمسار مسدود';
  }

  @override
  String get domainsWalletNotConnected => 'المحفظة غير متصلة';
}
