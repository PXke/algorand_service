// ignore: unused_import
import 'package:intl/intl.dart' as intl;
import 'app_localizations.dart';

// ignore_for_file: type=lint

/// The translations for Persian (`fa`).
class AppLocalizationsFa extends AppLocalizations {
  AppLocalizationsFa([String locale = 'fa']) : super(locale);

  @override
  String get appTitle => 'PXke Algorand';

  @override
  String get appTagline => 'پوشش مستقل اکوسیستم Algorand';

  @override
  String get navHome => 'خانه';

  @override
  String get navNews => 'اخبار';

  @override
  String get navSources => 'منابع';

  @override
  String get navSuggestions => 'پیشنهادها';

  @override
  String get navSearch => 'جستجو';

  @override
  String get navAdmin => 'مدیریت';

  @override
  String get navProducts => 'محصولات';

  @override
  String get navWallet => 'کیف پول';

  @override
  String get navAppearance => 'ظاهر';

  @override
  String get pageTitleHome => 'PXke Algorand Projects';

  @override
  String get pageTitleArticle => 'مقاله';

  @override
  String get pageTitleNews => 'اخبار';

  @override
  String get pageTitleSources => 'منابع خبری';

  @override
  String get pageTitleSuggestions => 'پیشنهادها';

  @override
  String get pageTitleSearch => 'جستجو';

  @override
  String get pageTitleAdmin => 'مدیریت';

  @override
  String get backToFeed => 'بازگشت به فید';

  @override
  String get homeWelcome => 'پلتفرم اکوسیستم PXke Algorand';

  @override
  String get homeTagline =>
      'اخبار گزینش‌شده از تریگرهای on-chain و منابع کرال‌شده، پیشنهادهای جامعه و جستجو.';

  @override
  String get homeNewsDescription =>
      'فید گزینش‌شده مقالات که هنگام تغییر منابع رصدشده یا رویدادهای زنجیره منتشر می‌شوند.';

  @override
  String get homeSourcesDescription =>
      'کرالرهای ثبت‌شده Discord، Reddit و وب که خط لوله خبری را تغذیه می‌کنند.';

  @override
  String get homeSuggestionsDescription =>
      'ایده‌های جامعه را با احراز هویت مبتنی بر کیف پول ارسال و رای مثبت دهید.';

  @override
  String get homeSearchDescription => 'جستجوی تمام‌متن در مقالات منتشرشده.';

  @override
  String get homeOpenProduct => 'باز کردن';

  @override
  String get themeLight => 'روشن';

  @override
  String get themeDark => 'تیره';

  @override
  String get themeSystem => 'سیستم';

  @override
  String get themeSwitchToLight => 'تغییر به تم روشن';

  @override
  String get themeSwitchToDark => 'تغییر به تم تیره';

  @override
  String get localeSystem => 'زبان سیستم';

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
  String get navLanguage => 'زبان';

  @override
  String get walletConnected => 'متصل';

  @override
  String get walletDisconnect => 'قطع اتصال';

  @override
  String get walletSignInTitle => 'ورود با کیف پول';

  @override
  String get walletSignInBody =>
      'برای ارسال پیشنهادها و رای مثبت، یک کیف پول Algorand سازگار با WalletConnect را وصل کنید.';

  @override
  String get walletConnect => 'اتصال کیف پول';

  @override
  String get walletConnectFailed =>
      'اتصال ناموفق بود. دوباره تلاش کنید یا ابتدا دیالوگ جفت‌سازی را لغو کنید.';

  @override
  String get walletDialogTitle => 'کیف پول خود را وصل کنید';

  @override
  String get walletDialogBody =>
      'کد QR را با یک کیف پول Algorand سازگار با WalletConnect (Pera، Defly و غیره) اسکن کنید یا لینک را در موبایل کپی/باز کنید.';

  @override
  String get walletAwaitingApproval =>
      'کیف پول وصل شد. برای تأیید درخواست ورود به اپلیکیشن کیف پول برگردید، سپس اینجا بازگردید.';

  @override
  String get walletAwaitingApprovalTitle => 'تقریباً تمام شد';

  @override
  String get walletCopyUri => 'کپی URI';

  @override
  String get walletUriCopied => 'URI WalletConnect کپی شد';

  @override
  String get walletOpenWallet => 'باز کردن کیف پول';

  @override
  String get walletOpenFailed =>
      'باز کردن اپلیکیشن کیف پول ممکن نشد — URI را کپی و در Pera یا Defly جای‌گذاری کنید';

  @override
  String get walletCancel => 'لغو';

  @override
  String get walletDone => 'انجام شد';

  @override
  String get walletErrorTitle => 'ورود ناموفق';

  @override
  String get walletErrorTimeout =>
      'مهلت درخواست ورود به پایان رسید. اپلیکیشن کیف پول را باز کرده و دوباره تلاش کنید.';

  @override
  String get walletErrorRejected => 'درخواست در کیف پول رد شد.';

  @override
  String get walletErrorGeneric =>
      'تکمیل ورود ممکن نشد. اتصال خود را بررسی کرده و دوباره تلاش کنید.';

  @override
  String get walletRetry => 'تلاش دوباره';

  @override
  String get walletShowQr => 'نمایش کد QR';

  @override
  String get walletMobileHint =>
      'دکمه زیر را بزنید تا کیف پول Algorand باز شود، اتصال را تأیید کنید و سپس اینجا بازگردید.';

  @override
  String get walletSignExplainer =>
      'کیف پول از شما می‌خواهد یک پیام ورود را امضا کنید (کیف پول‌های قدیمی‌تر به‌جای آن تراکنش 0-ALGO نشان می‌دهند). امضا رایگان است و چیزی به شبکه ارسال نمی‌شود — فقط مالکیت آدرس را اثبات می‌کند.';

  @override
  String get newsFeedTitle => 'آخرین مقالات';

  @override
  String get newsSubtitleDefault =>
      'تریگرهای on-chain و منابع کرال‌شده هنگام تغییر محتوا اینجا منتشر می‌شوند.';

  @override
  String get articlePublicationDetailsHint => 'ناشر، تاریخ و لینک منبع';

  @override
  String get newsPriceUnavailable =>
      'قیمت ALGO در دسترس نیست — جمع‌آوری معیارهای قیمت را اجرا کنید';

  @override
  String newsArticleCount(int count) {
    return '$count مقاله در فید گزینش‌شده';
  }

  @override
  String get newsEmptyFilteredTitle => 'هنوز مقاله‌ای نیست';

  @override
  String get newsEmptyTitle => 'فید خالی است';

  @override
  String get newsSponsoredLabel => 'حمایت‌شده';

  @override
  String newsSponsoredBy(String sponsor) {
    return 'حمایت‌شده · $sponsor';
  }

  @override
  String get newsEmptyFilteredMessage =>
      'این منبع هیچ مقاله‌ای منتشر نکرده است. مطمئن شوید کارگرها در حال نظرسنجی هستند و محتوا تغییر کرده است.';

  @override
  String get newsEmptyMessage =>
      'Conduit، کارگرهای Celery و service_registry را راه‌اندازی کنید تا فید پر شود.';

  @override
  String newsFilterShowing(String serviceId) {
    return 'نمایش مقالات از $serviceId';
  }

  @override
  String get clearFilter => 'پاک کردن';

  @override
  String get articleUntitled => 'بدون عنوان';

  @override
  String get articleSectionTitle => 'مقاله';

  @override
  String get articlePublicationDetails => 'جزئیات انتشار';

  @override
  String get articleMetaService => 'سرویس';

  @override
  String get articleMetaPublisher => 'ناشر';

  @override
  String get articleMetaDataSource => 'منبع داده';

  @override
  String get articleMetaCoinGecko => 'داده‌های بازار CoinGecko';

  @override
  String get articleMetaPublished => 'منتشرشده';

  @override
  String get articleMetaRound => 'راند';

  @override
  String get articleMetaTriggerTx => 'تراکنش تریگر';

  @override
  String get articleMetaSourceUrl => 'URL منبع';

  @override
  String get articleOpenInBrowser => 'باز کردن در مرورگر';

  @override
  String get articleViewOnExplorer => 'مشاهده در اکسپلورر Algorand';

  @override
  String articleMoreFrom(String serviceId) {
    return 'بیشتر از $serviceId';
  }

  @override
  String get articleViewSource => 'مشاهده منبع';

  @override
  String get adminTitle => 'مدیریت منابع';

  @override
  String get adminSubtitle =>
      'کرالرها و فیدهای ثبت‌شده (فقط برای کیف پول‌های مدیر قابل مشاهده).';

  @override
  String get adminViewFeedArticles => 'مشاهده فید این منبع';

  @override
  String get adminAccessDenied =>
      'برای مدیریت منابع یک کیف پول مدیر وصل کنید. ADMIN_WALLET_ADDRESSES را هنگام ساخت تنظیم کنید.';

  @override
  String get sourcesTitle => 'منابع خبری';

  @override
  String get sourcesSubtitle =>
      'سرویس‌های ثبت‌شده که توسط کارگرها کرال می‌شوند. Discord و Reddit طبق برنامه نظرسنجی می‌شوند؛ تطابق‌های on-chain هنگام تغییر محتوای رصدشده انتشار را فعال می‌کنند.';

  @override
  String get sourcesEmptyTitle => 'منبعی پیکربندی نشده';

  @override
  String get sourcesEmptyMessage =>
      'service_registry را با فایل‌های TOML مربوط به Discord و Reddit پر کنید، سپس مهاجرت‌ها را اجرا کنید. منابع پس از ثبت در Cassandra اینجا ظاهر می‌شوند.';

  @override
  String get sourcesMetaServiceId => 'شناسه سرویس';

  @override
  String get sourcesMetaScrapeUrl => 'URL کرال';

  @override
  String get sourcesMetaMatchRule => 'قانون تطابق';

  @override
  String get sourcesViewArticles => 'مشاهده مقالات';

  @override
  String get sourcesDisabled => 'غیرفعال';

  @override
  String filterAll(int count) {
    return 'همه ($count)';
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
    return 'وب ($count)';
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
  String get sourceKindWeb => 'وب';

  @override
  String get sourceKindOnChain => 'On-chain';

  @override
  String get sourceKindUnknown => 'منبع';

  @override
  String get navContact => 'تماس';

  @override
  String get contactTitle => 'تماس';

  @override
  String get contactSubtitle =>
      'اصلاحات، نکات یا بازخورد — به اتاق خبر بنویسید.';

  @override
  String get contactNameLabel => 'نام شما (اختیاری)';

  @override
  String get contactEmailLabel => 'ایمیل (اختیاری، در صورت تمایل به پاسخ)';

  @override
  String get contactMessageLabel => 'پیام';

  @override
  String get contactMessageHint => 'اصلاحات، نکات، بازخورد…';

  @override
  String get contactSend => 'ارسال پیام';

  @override
  String get contactSent => 'پیام ارسال شد — از تماس شما سپاسگزاریم.';

  @override
  String get contactTooShort => 'لطفاً چند کلمه بیشتر بنویسید (حداقل ۱۰ حرف).';

  @override
  String get searchTitle => 'جستجو';

  @override
  String get searchSubtitle => 'در تمام مقالات منتشرشده جستجو کنید.';

  @override
  String get searchQueryLabel => 'عبارت جستجو';

  @override
  String get searchQueryHint => 'کلیدواژه‌ها، عناوین، خلاصه‌ها…';

  @override
  String get searchAction => 'جستجو';

  @override
  String searchEngine(String name) {
    return 'موتور: $name';
  }

  @override
  String get searchEmptyTitle => 'نتیجه‌ای نیست';

  @override
  String get searchEmptyMessage =>
      'کلیدواژه‌های دیگر امتحان کنید یا مطمئن شوید مقالات ایندکس شده‌اند.';

  @override
  String get searchErrorBackend =>
      'جستجو موقتاً در دسترس نیست. API و پایگاه داده را بررسی کنید.';

  @override
  String get suggestionsTitle => 'پیشنهادها';

  @override
  String get suggestionsSubtitle =>
      'پس از ارسال حداقل ۰٫۰۱ ALGO به خزانه پلتفرم ارسال کنید. رای مثبت از امضای off-chain کیف پول استفاده می‌کند.';

  @override
  String get suggestionsNewTitle => 'پیشنهاد جدید';

  @override
  String get suggestionsFieldTitle => 'عنوان';

  @override
  String get suggestionsFieldBody => 'متن';

  @override
  String get suggestionsFieldTxid => 'شناسه تراکنش ارسال';

  @override
  String get suggestionsSubmit => 'ارسال پیشنهاد';

  @override
  String get suggestionsUpvoteTitle => 'رای مثبت';

  @override
  String get suggestionsSignatureLabel => 'امضا (base64)';

  @override
  String get suggestionsSignatureHint =>
      'پس از امضای پیام آماده‌شده در کیف پول';

  @override
  String get suggestionsSubmitUpvote => 'ثبت رای مثبت';

  @override
  String get suggestionsPrepareUpvote => 'آماده‌سازی رای مثبت';

  @override
  String get suggestionsUpvoteDialogTitle => 'امضای پیام رای مثبت';

  @override
  String get suggestionsCopyMessage => 'کپی پیام';

  @override
  String get suggestionsMessageCopied => 'پیام امضا در کلیپ‌بورد کپی شد';

  @override
  String suggestionsTreasuryHelp(String minAlgo, String address) {
    return 'قبل از ارسال حداقل $minAlgo ALGO به خزانه پلتفرم بفرستید:\n$address';
  }

  @override
  String suggestionsUpvoteCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count رای مثبت',
      one: '۱ رای مثبت',
      zero: 'هنوز رای مثبت ندارد',
    );
    return '$_temp0';
  }

  @override
  String get close => 'بستن';

  @override
  String suggestionsTxShort(String txid) {
    return 'Tx $txid';
  }

  @override
  String get snackConnectWallet => 'ابتدا کیف پول خود را وصل کنید';

  @override
  String get snackSuggestionSubmitted => 'پیشنهاد ارسال شد';

  @override
  String get snackUpvoteRecorded => 'رای مثبت ثبت شد';

  @override
  String get snackChooseSuggestionUpvote =>
      'ابتدا یک پیشنهاد برای رای مثبت انتخاب کنید';

  @override
  String metaPublishedEpoch(String epoch) {
    return 'منتشرشده: $epoch';
  }

  @override
  String metaPublishedRelative(String when) {
    return 'منتشرشده: $when';
  }

  @override
  String get timeJustNow => 'همین الان';

  @override
  String timeMinutesAgo(int count) {
    return '$count دقیقه پیش';
  }

  @override
  String timeHoursAgo(int count) {
    return '$count ساعت پیش';
  }

  @override
  String timeDaysAgo(int count) {
    return '$count روز پیش';
  }

  @override
  String metaRound(String round) {
    return 'راند: $round';
  }

  @override
  String metaService(String serviceId) {
    return 'سرویس: $serviceId';
  }

  @override
  String get navFrontPage => 'صفحه اصلی';

  @override
  String get navLatest => 'آخرین';

  @override
  String get navSections => 'بخش‌ها';

  @override
  String get navAbout => 'درباره';

  @override
  String get navApps => 'اپلیکیشن‌ها';

  @override
  String get navProductsMenuHint => 'کاوش پلتفرم';

  @override
  String get frontPageTopStories => 'خبرهای برتر';

  @override
  String get frontPageLatest => 'آخرین';

  @override
  String get frontPageMore => 'بیشتر از اتاق خبر';

  @override
  String frontPageSectionStories(String section) {
    return 'بیشتر در $section';
  }

  @override
  String get navHot => 'پربازدید';

  @override
  String get navTopics => 'موضوع‌ها';

  @override
  String get hotTitle => 'پربازدیدترین‌ها';

  @override
  String get hotLead => 'گزارش‌هایی که خوانندگان اکنون بیش از همه باز می‌کنند.';

  @override
  String get topicsTitle => 'موضوع‌ها';

  @override
  String get topicsLead =>
      'همهٔ برچسب‌هایی که تحریریه اخیراً به کار برده — اندازه بر اساس پوشش و رنگ بر اساس بازدید.';

  @override
  String topicSubtitle(String tag) {
    return 'گزارش‌های دارای برچسب «$tag»';
  }

  @override
  String readsCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count بازدید',
    );
    return '$_temp0';
  }

  @override
  String get sectionMarkets => 'بازارها';

  @override
  String get sectionSecurity => 'امنیت';

  @override
  String get sectionDevelopers => 'توسعه‌دهندگان';

  @override
  String get sectionCommunity => 'جامعه';

  @override
  String get sectionEcosystem => 'اکوسیستم';

  @override
  String get sectionEmptyTitle => 'هنوز چیزی اینجا نیست';

  @override
  String get sectionEmptyMessage =>
      'هنوز خبری در این بخش منتشر نشده است. به‌زودی سر بزنید.';

  @override
  String get bylineNewsroom => 'اتاق خبر';

  @override
  String get bylineMarketsDesk => 'میز بازار';

  @override
  String get bylineChainDesk => 'میز On-chain';

  @override
  String articleByline(String desk) {
    return 'نویسنده: $desk';
  }

  @override
  String articleReadingTime(int count) {
    return '$count دقیقه مطالعه';
  }

  @override
  String get articleRelatedTitle => 'داستان‌های مرتبط';

  @override
  String get articleShare => 'اشتراک‌گذاری';

  @override
  String get articleShareCopyLink => 'کپی پیوند';

  @override
  String get articleLinkCopied => 'لینک در کلیپ‌بورد کپی شد';

  @override
  String get footerTagline => 'پوشش مستقل و خودکار اکوسیستم Algorand.';

  @override
  String get footerSectionsHeading => 'بخش‌ها';

  @override
  String get footerAboutHeading => 'درباره';

  @override
  String get footerFollowHeading => 'دنبال کنید';

  @override
  String footerRights(String year) {
    return '© $year PXke Algorand. پوشش مستقل اکوسیستم Algorand.';
  }

  @override
  String get aboutTitle => 'درباره PXke Algorand';

  @override
  String get aboutLead =>
      'PXke Algorand یک اتاق خبر مستقل است که بلاک‌چین Algorand — بازارها، پروتکل، حکمرانی و پروژه‌های ساخته‌شده بر آن — را پوشش می‌دهد.';

  @override
  String get aboutHowHeading => 'چگونه منتشر می‌کنیم';

  @override
  String get aboutHowBody =>
      'پوشش ما به‌صورت خودکار از رویدادهای on-chain، داده‌های بازار زمان‌بندی‌شده و منابع رصدشده جامعه جمع‌آوری و سپس توسط خط لوله تحریریه در بخش‌ها سازماندهی می‌شود. هر داستان یک خط منشأ دارد تا بتوانید منبع آن را ردیابی کنید.';

  @override
  String get aboutAiHeading => 'نوشته‌شده با هوش مصنوعی';

  @override
  String get aboutAiBody =>
      'PXke Algorand روزنامه‌نگاری با کمک هوش مصنوعی منتشر می‌کند. مقالات ما توسط مدل‌های زبانی هوش مصنوعی از رویدادهای on-chain، داده‌های بازار و منابع جامعه فوق، تحت بازبینی تحریریه خودکار، تهیه می‌شوند — آن‌ها توسط ماشین تولید شده‌اند نه خبرنگاران انسانی. در هر داستان به منابع اصلی لینک می‌دهیم تا بتوانید هر ادعا را تأیید کنید و نویسنده رسمی سازمان است، نه یک نام مستعار فردی.';

  @override
  String get aboutProvenanceHeading => 'منشأ و شفافیت';

  @override
  String get aboutProvenanceBody =>
      'داستان‌های on-chain توسط تراکنش‌های قابل تأیید فعال می‌شوند و به اکسپلورر Algorand لینک دارند. داستان‌های بازار از داده‌های CoinGecko استفاده می‌کنند. جایگذاری‌های حمایت‌شده همیشه برچسب‌گذاری می‌شوند و به‌وضوح از محتوای تحریریه جدا نگه داشته می‌شوند.';

  @override
  String get aboutStandardsHeading => 'استانداردهای تحریریه';

  @override
  String get aboutStandardsBody =>
      'ما به دقت و نسبت‌دهی روشن متعهدیم. این نشریه مشاوره سرمایه‌گذاری نیست. خطایی دیدید؟ از طریق لینک‌های منبع در هر داستان با ما تماس بگیرید.';

  @override
  String get seedsTitle => 'بذرها';

  @override
  String get seedsSubtitle =>
      'نقاط شروع پیکربندی‌شده دستی برای کرالر. دامنه‌های تأییدشده از کشف در تب دامنه‌ها قرار دارند.';

  @override
  String get sourcesAdd => 'افزودن منبع';

  @override
  String get sourcesEdit => 'ویرایش';

  @override
  String get sourcesEditTitle => 'ویرایش منبع';

  @override
  String get sourcesAddTitle => 'افزودن منبع';

  @override
  String get sourcesDeleteTitle => 'منبع حذف شود؟';

  @override
  String sourcesDeleteBody(String serviceId) {
    return '«$serviceId» دیگر کرال نخواهد شد. مقالات منتشرشده باقی می‌مانند.';
  }

  @override
  String get sourcesDelete => 'حذف';

  @override
  String get sourcesSave => 'ذخیره';

  @override
  String get sourcesAddAction => 'افزودن';

  @override
  String get sourcesRequiredFields => 'شناسه سرویس، نام و URL الزامی هستند.';

  @override
  String get sourcesChangesNextPoll =>
      'تغییرات در نظرسنجی بعدی کرالر اعمال می‌شوند.';

  @override
  String get sourcesMerge => 'ادغام';

  @override
  String get sourcesMergeTitle => 'ادغام سرویس‌ها';

  @override
  String get sourcesMergeIntro =>
      'چند سرویس را در یکی ادغام کنید. منابع و دامنه‌های سرویس‌های انتخاب‌شده به هدف منتقل می‌شوند؛ سرویس‌های خالی غیرفعال می‌شوند. وقتی یک محصول چند دامنه دارد (مثلاً algorand.co + algorand.com) از این استفاده کنید.';

  @override
  String get sourcesMergeTarget => 'نگه‌داشتن به‌عنوان هدف';

  @override
  String get sourcesMergeFold => 'ادغام (غیرفعال خواهد شد)';

  @override
  String get sourcesMergeAction => 'ادغام';

  @override
  String get sourcesMergeNeedsTwo =>
      'یک هدف و حداقل یک سرویس برای ادغام انتخاب کنید.';

  @override
  String sourcesMergeDone(int count, String target) {
    return '$count سرویس در $target ادغام شد.';
  }

  @override
  String get sourcesFieldServiceId => 'شناسه سرویس';

  @override
  String get sourcesFieldServiceIdHint =>
      'kebab-case، مثلاً algorand-foundation-blog';

  @override
  String get sourcesFieldDisplayName => 'نام نمایشی';

  @override
  String get sourcesFieldScrapeUrl => 'URL کرال';

  @override
  String get sourcesFieldScrapeUrlHint =>
      'https://…، reddit://r/…، discord://channel/…';

  @override
  String get sourcesFieldMatchKind => 'نوع تطابق';

  @override
  String get sourcesFieldMatchKindHint => 'address / app_id / asset_id';

  @override
  String get sourcesFieldMatchValue => 'مقدار تطابق';

  @override
  String get sourcesFieldMatchValueHint => 'مثلاً آدرس کیف پول یا app id';

  @override
  String get sourcesMatchRuleHelp =>
      'قانون تطابق این منبع را به فعالیت on-chain پیوند می‌دهد: وقتی کرالر زنجیره تراکنش MainNet را می‌بیند که آدرس فرستنده/گیرنده (نوع «address»)، شناسه اپلیکیشن («app_id») یا شناسه دارایی («asset_id») با مقدار تطابق برابر است، رویداد را به این منبع نسبت می‌دهد و می‌تواند مقاله منتشر کند. برای منابع وب یا Reddit صرفاً اطلاعاتی است — چیزی توصیفی مثل دامنه یا ساب‌ردیت بنویسید.';

  @override
  String get sourcesEnabled => 'فعال';

  @override
  String get actionCancel => 'لغو';

  @override
  String get actionRefresh => 'تازه‌سازی';

  @override
  String get adminTabSeeds => 'بذرها';

  @override
  String get adminTabArticles => 'مقالات';

  @override
  String get adminTabWriterBriefs => 'خلاصه‌های نویسنده';

  @override
  String get adminTabClassifier => 'طبقه‌بند';

  @override
  String get adminTabDomains => 'دامنه‌ها';

  @override
  String get adminTabToolInsights => 'بینش ابزار';

  @override
  String get adminTabSessions => 'نشست‌ها';

  @override
  String get adminTabSystem => 'سیستم';

  @override
  String get domainsIntro =>
      'مرز کرال: دامنه‌هایی که کرالر لینک با آن‌ها مواجه شده است. بن‌بست‌ها هرگز کاوش نمی‌شوند — به‌صورت خودکار با امتیاز مرتبط و حکم‌های بازبینی شما یا دستی اینجا تنظیم می‌شوند.';

  @override
  String get domainsFilterAll => 'همه';

  @override
  String domainsFilterAllCount(int count) {
    return 'همه ($count)';
  }

  @override
  String get domainsFilterPending => 'در انتظار بازبینی';

  @override
  String domainsFilterPendingCount(int count) {
    return 'در انتظار بازبینی ($count)';
  }

  @override
  String get domainsFilterDeadEnds => 'بن‌بست‌ها';

  @override
  String domainsFilterDeadEndsCount(int count) {
    return 'بن‌بست‌ها ($count)';
  }

  @override
  String get domainsEmptyTitle => 'هنوز دامنه‌ای نیست';

  @override
  String get domainsEmptyMessage =>
      'کرالر هر دامنه‌ای را که هنگام دنبال کردن لینک‌ها می‌بیند ثبت می‌کند.';

  @override
  String domainsOpenInNewTab(String domain) {
    return 'باز کردن $domain در تب جدید';
  }

  @override
  String domainsKeywords(String keywords) {
    return 'کلیدواژه‌ها: $keywords';
  }

  @override
  String domainsLinkedAs(String text) {
    return 'لینک‌شده به‌عنوان «$text»';
  }

  @override
  String domainsPredictedInterest(String score) {
    return 'علاقه پیش‌بینی‌شده $score';
  }

  @override
  String domainsFoundOn(String url) {
    return 'یافت‌شده در $url';
  }

  @override
  String domainsScore(String score) {
    return 'امتیاز $score';
  }

  @override
  String domainsCrawled(String date) {
    return 'کرال‌شده $date';
  }

  @override
  String domainsPagesCrawled(int count) {
    return '$count صفحه';
  }

  @override
  String get domainsDeadEnd => 'بن‌بست';

  @override
  String get domainsApproveExplore => 'کل سایت';

  @override
  String get domainsAddButton => 'افزودن';

  @override
  String get domainsAddSinglePageOnly => 'تک‌صفحه';

  @override
  String get domainsScoreUnexplained => 'جزئیات امتیاز در دسترس نیست';

  @override
  String get domainsPossibleService => 'احتمالاً یک سرویس؟';

  @override
  String get domainsPossibleServiceHint =>
      'امتیاز خوبی دارد اما به‌عنوان خبر/عمومی برچسب خورده — ممکن است یک محصول واقعی باشد، نه فقط منبع استناد. به‌جای «تک‌صفحه» به «کل سایت» فکر کنید.';

  @override
  String domainsSuggestedHint(int count) {
    return 'پیشنهادی — $count صفحه هم‌دامنه یافت شد';
  }

  @override
  String get paginationPrevious => 'قبلی';

  @override
  String get paginationNext => 'بعدی';

  @override
  String paginationPageOf(int page, int total) {
    return 'صفحه $page از $total';
  }

  @override
  String get domainsMarkDeadEnd => 'علامت‌گذاری به‌عنوان بن‌بست';

  @override
  String get domainsRevive => 'احیا';

  @override
  String domainsApprovedSnack(String domain) {
    return '$domain تأیید شد — کرالر می‌تواند آن را کاوش کند';
  }

  @override
  String domainsDeadEndSnack(String domain) {
    return '$domain به‌عنوان بن‌بست علامت‌گذاری شد';
  }

  @override
  String get domainsWalletNotConnected => 'کیف پول وصل نیست';

  @override
  String get frontPageMoreNews => 'خبرهای بیشتر';

  @override
  String storiesCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count گزارش',
    );
    return '$_temp0';
  }

  @override
  String get byTheNumbersRange => '۷ روز گذشته';

  @override
  String get byTheNumbersMarketCap => 'ارزش بازار';

  @override
  String get byTheNumbersVolume => 'حجم ۲۴ ساعته';

  @override
  String get hotTabHot => 'داغ در حال حاضر';

  @override
  String get hotTabAllTime => 'همه دوران';
}
