// ignore: unused_import
import 'package:intl/intl.dart' as intl;
import 'app_localizations.dart';

// ignore_for_file: type=lint

/// The translations for Russian (`ru`).
class AppLocalizationsRu extends AppLocalizations {
  AppLocalizationsRu([String locale = 'ru']) : super(locale);

  @override
  String get appTitle => 'PXke Algorand';

  @override
  String get appTagline => 'Независимое освещение экосистемы Algorand';

  @override
  String get navHome => 'Главная';

  @override
  String get navNews => 'Новости';

  @override
  String get navSources => 'Источники';

  @override
  String get navSuggestions => 'Предложения';

  @override
  String get navSearch => 'Поиск';

  @override
  String get navAdmin => 'Админ';

  @override
  String get navProducts => 'ПРОДУКТЫ';

  @override
  String get navWallet => 'Кошелёк';

  @override
  String get navAppearance => 'ОФОРМЛЕНИЕ';

  @override
  String get pageTitleHome => 'PXke Algorand Projects';

  @override
  String get pageTitleArticle => 'Статья';

  @override
  String get pageTitleNews => 'Новости';

  @override
  String get pageTitleSources => 'Источники новостей';

  @override
  String get pageTitleSuggestions => 'Предложения';

  @override
  String get pageTitleSearch => 'Поиск';

  @override
  String get pageTitleAdmin => 'Админ';

  @override
  String get backToFeed => 'Назад к ленте';

  @override
  String get homeWelcome => 'Платформа экосистемы PXke Algorand';

  @override
  String get homeTagline =>
      'Подборка новостей из on-chain-триггеров и обходимых источников, предложения сообщества и поиск.';

  @override
  String get homeNewsDescription =>
      'Подборка статей, публикуемых при изменении отслеживаемых источников или событий в сети.';

  @override
  String get homeSourcesDescription =>
      'Зарегистрированные краулеры Discord, Reddit и веб-источников, питающие новостной конвейер.';

  @override
  String get homeSuggestionsDescription =>
      'Отправляйте и голосуйте за идеи сообщества с аутентификацией через кошелёк.';

  @override
  String get homeSearchDescription =>
      'Полнотекстовый поиск по опубликованным статьям.';

  @override
  String get homeOpenProduct => 'Открыть';

  @override
  String get themeLight => 'Светлая';

  @override
  String get themeDark => 'Тёмная';

  @override
  String get themeSystem => 'Системная';

  @override
  String get themeSwitchToLight => 'Переключить на светлую тему';

  @override
  String get themeSwitchToDark => 'Переключить на тёмную тему';

  @override
  String get localeSystem => 'Язык системы';

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
  String get navLanguage => 'ЯЗЫК';

  @override
  String get walletConnected => 'Подключён';

  @override
  String get walletDisconnect => 'Отключить';

  @override
  String get walletSignInTitle => 'Войти через кошелёк';

  @override
  String get walletSignInBody =>
      'Подключите кошелёк Algorand с поддержкой WalletConnect, чтобы отправлять предложения и голоса.';

  @override
  String get walletConnect => 'Подключить кошелёк';

  @override
  String get walletConnectFailed =>
      'Не удалось подключиться. Попробуйте снова или сначала отмените диалог сопряжения.';

  @override
  String get walletDialogTitle => 'Подключите кошелёк';

  @override
  String get walletDialogBody =>
      'Отсканируйте QR-код кошельком Algorand с WalletConnect (Pera, Defly и др.) или скопируйте/откройте ссылку на телефоне.';

  @override
  String get walletAwaitingApproval =>
      'Кошелёк подключён. Вернитесь в приложение кошелька, чтобы подтвердить запрос на вход, затем вернитесь сюда.';

  @override
  String get walletAwaitingApprovalTitle => 'Почти готово';

  @override
  String get walletCopyUri => 'Копировать URI';

  @override
  String get walletUriCopied => 'URI WalletConnect скопирован';

  @override
  String get walletOpenWallet => 'Открыть кошелёк';

  @override
  String get walletOpenFailed =>
      'Не удалось открыть кошелёк — скопируйте URI и вставьте в Pera или Defly';

  @override
  String get walletCancel => 'Отмена';

  @override
  String get walletDone => 'Готово';

  @override
  String get walletErrorTitle => 'Ошибка входа';

  @override
  String get walletErrorTimeout =>
      'Время ожидания запроса на вход истекло. Откройте кошелёк и попробуйте снова.';

  @override
  String get walletErrorRejected => 'Запрос отклонён в кошельке.';

  @override
  String get walletErrorGeneric =>
      'Не удалось завершить вход. Проверьте подключение и попробуйте снова.';

  @override
  String get walletRetry => 'Повторить';

  @override
  String get walletShowQr => 'Показать QR-код';

  @override
  String get walletMobileHint =>
      'Нажмите кнопку ниже, чтобы открыть кошелёк Algorand, подтвердите подключение и вернитесь сюда.';

  @override
  String get walletSignExplainer =>
      'Кошелёк попросит подписать сообщение для входа (в старых кошельках вместо этого показывается транзакция на 0 ALGO). Подпись бесплатна и ничего не отправляется в сеть — она лишь подтверждает владение адресом.';

  @override
  String get newsFeedTitle => 'Последние статьи';

  @override
  String get newsSubtitleDefault =>
      'On-chain-триггеры и обходимые источники публикуют здесь при изменении контента.';

  @override
  String get articlePublicationDetailsHint =>
      'Издатель, дата и ссылка на источник';

  @override
  String get newsPriceUnavailable =>
      'Цена ALGO недоступна — запустите сбор ценовых метрик';

  @override
  String newsArticleCount(int count) {
    return '$count статей в подборке';
  }

  @override
  String get newsEmptyFilteredTitle => 'Пока нет статей';

  @override
  String get newsEmptyTitle => 'Лента пуста';

  @override
  String get newsSponsoredLabel => 'Реклама';

  @override
  String newsSponsoredBy(String sponsor) {
    return 'Реклама · $sponsor';
  }

  @override
  String get newsEmptyFilteredMessage =>
      'Этот источник ещё не опубликовал статей. Убедитесь, что воркеры опрашивают источник и контент изменился.';

  @override
  String get newsEmptyMessage =>
      'Запустите Conduit, воркеры Celery и заполните service_registry, чтобы наполнить ленту.';

  @override
  String newsFilterShowing(String serviceId) {
    return 'Статьи из $serviceId';
  }

  @override
  String get clearFilter => 'Сбросить';

  @override
  String get articleUntitled => 'Без названия';

  @override
  String get articleSectionTitle => 'Статья';

  @override
  String get articlePublicationDetails => 'Сведения о публикации';

  @override
  String get articleMetaService => 'Сервис';

  @override
  String get articleMetaPublisher => 'Издатель';

  @override
  String get articleMetaDataSource => 'Источник данных';

  @override
  String get articleMetaCoinGecko => 'Рыночные данные CoinGecko';

  @override
  String get articleMetaPublished => 'Опубликовано';

  @override
  String get articleMetaRound => 'Раунд';

  @override
  String get articleMetaTriggerTx => 'Триггерная tx';

  @override
  String get articleMetaSourceUrl => 'URL источника';

  @override
  String get articleOpenInBrowser => 'Открыть в браузере';

  @override
  String get articleViewOnExplorer => 'Смотреть в обозревателе Algorand';

  @override
  String articleMoreFrom(String serviceId) {
    return 'Ещё от $serviceId';
  }

  @override
  String get articleViewSource => 'Смотреть источник';

  @override
  String get adminTitle => 'Администрирование источников';

  @override
  String get adminSubtitle =>
      'Зарегистрированные краулеры и фиды (видны только админ-кошелькам).';

  @override
  String get adminViewFeedArticles => 'Смотреть ленту этого источника';

  @override
  String get adminAccessDenied =>
      'Подключите админ-кошелёк для управления источниками. Укажите ADMIN_WALLET_ADDRESSES при сборке.';

  @override
  String get sourcesTitle => 'Источники новостей';

  @override
  String get sourcesSubtitle =>
      'Зарегистрированные сервисы, обходимые воркерами. Discord и Reddit опрашиваются по расписанию; совпадения on-chain публикуются при изменении отслеживаемого контента.';

  @override
  String get sourcesEmptyTitle => 'Источники не настроены';

  @override
  String get sourcesEmptyMessage =>
      'Заполните service_registry TOML-файлами Discord и Reddit, затем выполните миграции. Источники появятся здесь после регистрации в Cassandra.';

  @override
  String get sourcesMetaServiceId => 'ID сервиса';

  @override
  String get sourcesMetaScrapeUrl => 'URL для обхода';

  @override
  String get sourcesMetaMatchRule => 'Правило совпадения';

  @override
  String get sourcesViewArticles => 'Смотреть статьи';

  @override
  String get sourcesDisabled => 'Отключён';

  @override
  String filterAll(int count) {
    return 'Все ($count)';
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
    return 'Веб ($count)';
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
  String get sourceKindWeb => 'Веб';

  @override
  String get sourceKindOnChain => 'On-chain';

  @override
  String get sourceKindUnknown => 'Источник';

  @override
  String get navContact => 'Контакты';

  @override
  String get contactTitle => 'Контакты';

  @override
  String get contactSubtitle =>
      'Исправления, подсказки или отзывы — напишите в редакцию.';

  @override
  String get contactNameLabel => 'Ваше имя (необязательно)';

  @override
  String get contactEmailLabel => 'Email (необязательно, если хотите ответ)';

  @override
  String get contactMessageLabel => 'Сообщение';

  @override
  String get contactMessageHint => 'Исправления, подсказки, отзывы…';

  @override
  String get contactSend => 'Отправить сообщение';

  @override
  String get contactSent => 'Сообщение отправлено — спасибо, что написали нам.';

  @override
  String get contactTooShort =>
      'Напишите чуть подробнее (минимум 10 символов).';

  @override
  String get searchTitle => 'Поиск';

  @override
  String get searchSubtitle => 'Поиск по всем опубликованным статьям.';

  @override
  String get searchQueryLabel => 'Поисковый запрос';

  @override
  String get searchQueryHint => 'Ключевые слова, заголовки, описания…';

  @override
  String get searchAction => 'Искать';

  @override
  String searchEngine(String name) {
    return 'Движок: $name';
  }

  @override
  String get searchEmptyTitle => 'Ничего не найдено';

  @override
  String get searchEmptyMessage =>
      'Попробуйте другие ключевые слова или проверьте, что статьи проиндексированы.';

  @override
  String get searchErrorBackend =>
      'Поиск временно недоступен. Проверьте, что API и база данных запущены.';

  @override
  String get suggestionsTitle => 'Предложения';

  @override
  String get suggestionsSubtitle =>
      'Отправляйте после перевода не менее 0,01 ALGO в казну платформы. Голоса используют off-chain-подпись кошелька.';

  @override
  String get suggestionsNewTitle => 'Новое предложение';

  @override
  String get suggestionsFieldTitle => 'Заголовок';

  @override
  String get suggestionsFieldBody => 'Текст';

  @override
  String get suggestionsFieldTxid => 'ID транзакции отправки';

  @override
  String get suggestionsSubmit => 'Отправить предложение';

  @override
  String get suggestionsUpvoteTitle => 'Голос';

  @override
  String get suggestionsSignatureLabel => 'Подпись (base64)';

  @override
  String get suggestionsSignatureHint =>
      'После подписи подготовленного сообщения в кошельке';

  @override
  String get suggestionsSubmitUpvote => 'Отправить голос';

  @override
  String get suggestionsPrepareUpvote => 'Подготовить голос';

  @override
  String get suggestionsUpvoteDialogTitle => 'Подписать сообщение для голоса';

  @override
  String get suggestionsCopyMessage => 'Копировать сообщение';

  @override
  String get suggestionsMessageCopied =>
      'Сообщение для подписи скопировано в буфер обмена';

  @override
  String suggestionsTreasuryHelp(String minAlgo, String address) {
    return 'Перед отправкой переведите не менее $minAlgo ALGO в казну платформы:\n$address';
  }

  @override
  String suggestionsUpvoteCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count голосов',
      many: '$count голосов',
      few: '$count голоса',
      one: '1 голос',
      zero: 'Пока нет голосов',
    );
    return '$_temp0';
  }

  @override
  String get close => 'Закрыть';

  @override
  String suggestionsTxShort(String txid) {
    return 'Tx $txid';
  }

  @override
  String get snackConnectWallet => 'Сначала подключите кошелёк';

  @override
  String get snackSuggestionSubmitted => 'Предложение отправлено';

  @override
  String get snackUpvoteRecorded => 'Голос учтён';

  @override
  String get snackChooseSuggestionUpvote =>
      'Сначала выберите предложение для голосования';

  @override
  String metaPublishedEpoch(String epoch) {
    return 'Опубликовано: $epoch';
  }

  @override
  String metaPublishedRelative(String when) {
    return 'Опубликовано: $when';
  }

  @override
  String get timeJustNow => 'только что';

  @override
  String timeMinutesAgo(int count) {
    return '$count мин назад';
  }

  @override
  String timeHoursAgo(int count) {
    return '$count ч назад';
  }

  @override
  String timeDaysAgo(int count) {
    return '$count дн назад';
  }

  @override
  String metaRound(String round) {
    return 'Раунд: $round';
  }

  @override
  String metaService(String serviceId) {
    return 'Сервис: $serviceId';
  }

  @override
  String get navFrontPage => 'Главная страница';

  @override
  String get navLatest => 'Последние';

  @override
  String get navSections => 'РАЗДЕЛЫ';

  @override
  String get navAbout => 'О нас';

  @override
  String get navApps => 'Приложения';

  @override
  String get navProductsMenuHint => 'Изучить платформу';

  @override
  String get frontPageTopStories => 'Главные новости';

  @override
  String get frontPageLatest => 'Последние';

  @override
  String get frontPageMore => 'Ещё из редакции';

  @override
  String frontPageSectionStories(String section) {
    return 'Ещё в разделе «$section»';
  }

  @override
  String get navHot => 'Популярное';

  @override
  String get navTopics => 'Темы';

  @override
  String get hotTitle => 'Самое читаемое';

  @override
  String get hotLead =>
      'Материалы, которые читатели открывают чаще всего прямо сейчас.';

  @override
  String get topicsTitle => 'Темы';

  @override
  String get topicsLead =>
      'Все теги редакции за последнее время — размер отражает охват, цвет — чтения.';

  @override
  String topicSubtitle(String tag) {
    return 'Материалы с тегом «$tag»';
  }

  @override
  String readsCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count прочтений',
      few: '$count прочтения',
      one: '1 прочтение',
    );
    return '$_temp0';
  }

  @override
  String get sectionMarkets => 'Рынки';

  @override
  String get sectionSecurity => 'Безопасность';

  @override
  String get sectionDevelopers => 'Разработчики';

  @override
  String get sectionCommunity => 'Сообщество';

  @override
  String get sectionEcosystem => 'Экосистема';

  @override
  String get sectionEmptyTitle => 'Пока здесь пусто';

  @override
  String get sectionEmptyMessage =>
      'В этом разделе ещё нет материалов. Загляните позже.';

  @override
  String get bylineNewsroom => 'Редакция';

  @override
  String get bylineMarketsDesk => 'Рынки';

  @override
  String get bylineChainDesk => 'On-chain';

  @override
  String articleByline(String desk) {
    return 'Автор: $desk';
  }

  @override
  String articleReadingTime(int count) {
    return '$count мин чтения';
  }

  @override
  String get articleRelatedTitle => 'Похожие материалы';

  @override
  String get articleShare => 'Поделиться';

  @override
  String get articleLinkCopied => 'Ссылка скопирована в буфер обмена';

  @override
  String get footerTagline =>
      'Независимое автоматизированное освещение экосистемы Algorand.';

  @override
  String get footerSectionsHeading => 'Разделы';

  @override
  String get footerAboutHeading => 'О нас';

  @override
  String footerRights(String year) {
    return '© $year PXke Algorand. Независимое освещение экосистемы Algorand.';
  }

  @override
  String get aboutTitle => 'О PXke Algorand';

  @override
  String get aboutLead =>
      'PXke Algorand — независимая редакция, освещающая блокчейн Algorand: рынки, протокол, управление и проекты, строящиеся на нём.';

  @override
  String get aboutHowHeading => 'Как мы публикуем';

  @override
  String get aboutHowBody =>
      'Наши материалы собираются автоматически из on-chain-событий, запланированных рыночных данных и отслеживаемых источников сообщества, затем распределяются по разделам редакционным конвейером. У каждой статьи есть строка происхождения, чтобы вы могли проследить источник.';

  @override
  String get aboutAiHeading => 'Написано с помощью ИИ';

  @override
  String get aboutAiBody =>
      'PXke Algorand публикует журналистику с участием ИИ. Статьи создаются языковыми моделями на основе on-chain-событий, рыночных данных и источников сообщества под автоматической редакционной проверкой — они генерируются машиной, а не пишутся человеком-репортёром. Мы ссылаемся на оригинальные источники в каждой статье, чтобы вы могли проверить утверждения; автором записи выступает организация, а не отдельный журналист.';

  @override
  String get aboutProvenanceHeading => 'Происхождение и прозрачность';

  @override
  String get aboutProvenanceBody =>
      'On-chain-материалы запускаются проверяемыми транзакциями и ссылаются на обозреватель Algorand. Рыночные материалы используют данные CoinGecko. Рекламные размещения всегда помечаются и чётко отделены от редакционного контента.';

  @override
  String get aboutStandardsHeading => 'Редакционные стандарты';

  @override
  String get aboutStandardsBody =>
      'Мы стремимся к точности и ясной атрибуции. Это издание не является инвестиционной рекомендацией. Нашли ошибку? Свяжитесь с нами через ссылки на источники в любой статье.';

  @override
  String get seedsTitle => 'Сиды';

  @override
  String get seedsSubtitle =>
      'Вручную настроенные стартовые точки для краулера. Домены, одобренные при обнаружении, находятся на вкладке «Домены».';

  @override
  String get sourcesAdd => 'Добавить источник';

  @override
  String get sourcesEdit => 'Изменить';

  @override
  String get sourcesEditTitle => 'Изменить источник';

  @override
  String get sourcesAddTitle => 'Добавить источник';

  @override
  String get sourcesDeleteTitle => 'Удалить источник?';

  @override
  String sourcesDeleteBody(String serviceId) {
    return '«$serviceId» перестанет обходиться. Уже опубликованные статьи останутся.';
  }

  @override
  String get sourcesDelete => 'Удалить';

  @override
  String get sourcesSave => 'Сохранить';

  @override
  String get sourcesAddAction => 'Добавить';

  @override
  String get sourcesRequiredFields => 'Обязательны ID сервиса, имя и URL.';

  @override
  String get sourcesChangesNextPoll =>
      'Изменения применятся при следующем опросе краулера.';

  @override
  String get sourcesMerge => 'Объединить';

  @override
  String get sourcesMergeTitle => 'Объединить сервисы';

  @override
  String get sourcesMergeIntro =>
      'Сведите несколько сервисов в один. Источники и домены выбранных сервисов переходят к целевому; опустошённые сервисы отключаются. Используйте, когда один продукт охватывает несколько доменов (например, algorand.co + algorand.com).';

  @override
  String get sourcesMergeTarget => 'Оставить целевым';

  @override
  String get sourcesMergeFold => 'Включить (будет отключён)';

  @override
  String get sourcesMergeAction => 'Объединить';

  @override
  String get sourcesMergeNeedsTwo =>
      'Выберите целевой сервис и хотя бы один для включения.';

  @override
  String sourcesMergeDone(int count, String target) {
    return 'Объединено сервисов: $count в $target.';
  }

  @override
  String get sourcesFieldServiceId => 'ID сервиса';

  @override
  String get sourcesFieldServiceIdHint =>
      'kebab-case, напр. algorand-foundation-blog';

  @override
  String get sourcesFieldDisplayName => 'Отображаемое имя';

  @override
  String get sourcesFieldScrapeUrl => 'URL для обхода';

  @override
  String get sourcesFieldScrapeUrlHint =>
      'https://…, reddit://r/…, discord://channel/…';

  @override
  String get sourcesFieldMatchKind => 'Тип совпадения';

  @override
  String get sourcesFieldMatchKindHint => 'address / app_id / asset_id';

  @override
  String get sourcesFieldMatchValue => 'Значение совпадения';

  @override
  String get sourcesFieldMatchValueHint =>
      'напр. адрес кошелька или ID приложения';

  @override
  String get sourcesMatchRuleHelp =>
      'Правило совпадения связывает этот источник с on-chain-активностью: когда краулер сети видит транзакцию в MainNet, у которой адрес отправителя/получателя (тип «address»), ID приложения («app_id») или ID актива («asset_id») совпадает со значением, он относит событие к этому источнику и может опубликовать статью. Для веб- или Reddit-источников это только справочно — укажите что-то описательное, например домен или сабреддит.';

  @override
  String get sourcesEnabled => 'Включён';

  @override
  String get actionCancel => 'Отмена';

  @override
  String get actionRefresh => 'Обновить';

  @override
  String get adminTabSeeds => 'Сиды';

  @override
  String get adminTabArticles => 'Статьи';

  @override
  String get adminTabWriterBriefs => 'Брифы автора';

  @override
  String get adminTabClassifier => 'Классификатор';

  @override
  String get adminTabDomains => 'Домены';

  @override
  String get adminTabToolInsights => 'Инструменты';

  @override
  String get adminTabSessions => 'Сессии';

  @override
  String get adminTabSystem => 'Система';

  @override
  String get domainsIntro =>
      'Граница обхода: домены, которые встретил краулер ссылок. Тупики никогда не исследуются — отмечаются автоматически по релевантности и вашим вердиктам или вручную здесь.';

  @override
  String get domainsFilterAll => 'Все';

  @override
  String domainsFilterAllCount(int count) {
    return 'Все ($count)';
  }

  @override
  String get domainsFilterPending => 'На проверке';

  @override
  String domainsFilterPendingCount(int count) {
    return 'На проверке ($count)';
  }

  @override
  String get domainsFilterDeadEnds => 'Тупики';

  @override
  String domainsFilterDeadEndsCount(int count) {
    return 'Тупики ($count)';
  }

  @override
  String get domainsEmptyTitle => 'Пока нет доменов';

  @override
  String get domainsEmptyMessage =>
      'Краулер записывает каждый домен, который встречает при переходе по ссылкам.';

  @override
  String domainsOpenInNewTab(String domain) {
    return 'Открыть $domain в новой вкладке';
  }

  @override
  String domainsKeywords(String keywords) {
    return 'ключевые слова: $keywords';
  }

  @override
  String domainsLinkedAs(String text) {
    return 'ссылка с текстом «$text»';
  }

  @override
  String domainsPredictedInterest(String score) {
    return 'прогноз интереса $score';
  }

  @override
  String domainsFoundOn(String url) {
    return 'найден на $url';
  }

  @override
  String domainsScore(String score) {
    return 'оценка $score';
  }

  @override
  String domainsCrawled(String date) {
    return 'обход $date';
  }

  @override
  String domainsPagesCrawled(int count) {
    return '$count страниц';
  }

  @override
  String get domainsDeadEnd => 'Тупик';

  @override
  String get domainsApproveExplore => 'Одобрить и исследовать';

  @override
  String get domainsCrawlOnce => 'Обход один раз, без источника';

  @override
  String get domainsAddButton => 'Добавить';

  @override
  String get domainsAddAsSeed => 'Добавить как постоянный источник';

  @override
  String get domainsScoreUnexplained => 'Разбивка оценки недоступна';

  @override
  String get paginationPrevious => 'Назад';

  @override
  String get paginationNext => 'Далее';

  @override
  String paginationPageOf(int page, int total) {
    return 'Страница $page из $total';
  }

  @override
  String get domainsMarkDeadEnd => 'Отметить как тупик';

  @override
  String get domainsRevive => 'Восстановить';

  @override
  String domainsApprovedSnack(String domain) {
    return '$domain одобрен — краулер может его исследовать';
  }

  @override
  String domainsDeadEndSnack(String domain) {
    return '$domain отмечен как тупик';
  }

  @override
  String get domainsWalletNotConnected => 'Кошелёк не подключён';

  @override
  String get frontPageMoreNews => 'Ещё новости';

  @override
  String storiesCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count материалов',
      few: '$count материала',
      one: '1 материал',
    );
    return '$_temp0';
  }

  @override
  String get byTheNumbersRange => 'За 7 дней';

  @override
  String get byTheNumbersMarketCap => 'Капитализация';

  @override
  String get byTheNumbersVolume => 'Объём за 24ч';

  @override
  String get hotTabHot => 'Сейчас в тренде';

  @override
  String get hotTabAllTime => 'За всё время';
}
