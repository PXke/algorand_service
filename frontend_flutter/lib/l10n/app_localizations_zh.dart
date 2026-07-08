// ignore: unused_import
import 'package:intl/intl.dart' as intl;
import 'app_localizations.dart';

// ignore_for_file: type=lint

/// The translations for Chinese (`zh`).
class AppLocalizationsZh extends AppLocalizations {
  AppLocalizationsZh([String locale = 'zh']) : super(locale);

  @override
  String get appTitle => 'PXke 算法';

  @override
  String get appTagline => '独立覆盖Algorand生态系统';

  @override
  String get navHome => '家';

  @override
  String get navNews => '消息';

  @override
  String get navSources => '来源';

  @override
  String get navSuggestions => '建议';

  @override
  String get navSearch => '搜索';

  @override
  String get navAdmin => '行政';

  @override
  String get navProducts => '产品';

  @override
  String get navWallet => '钱包';

  @override
  String get navAppearance => '外貌';

  @override
  String get pageTitleHome => 'PXke Algorand 项目';

  @override
  String get pageTitleArticle => '文章';

  @override
  String get pageTitleNews => '消息';

  @override
  String get pageTitleSources => '新闻来源';

  @override
  String get pageTitleSuggestions => '建议';

  @override
  String get pageTitleSearch => '搜索';

  @override
  String get pageTitleAdmin => '行政';

  @override
  String get backToFeed => '返回饲料';

  @override
  String get homeWelcome => 'PXke Algorand 生态系统平台';

  @override
  String get homeTagline => '来自链上触发器和爬取来源、社区建议和搜索的精选新闻。';

  @override
  String get homeNewsDescription => '当受监控的来源或连锁事件发生变化时发布的文章的精选提要。';

  @override
  String get homeSourcesDescription => '注册了 Discord、Reddit 和网络爬虫，为新闻管道提供动力。';

  @override
  String get homeSuggestionsDescription => '通过钱包支持的身份验证提交并支持社区想法。';

  @override
  String get homeSearchDescription => '对已发表文章进行全文搜索。';

  @override
  String get homeOpenProduct => '打开';

  @override
  String get themeLight => '光';

  @override
  String get themeDark => '黑暗的';

  @override
  String get themeSystem => '系统';

  @override
  String get themeSwitchToLight => '切换到浅色主题';

  @override
  String get themeSwitchToDark => '切换到深色主题';

  @override
  String get localeSystem => '系统语言';

  @override
  String get localeEnglish => '英语';

  @override
  String get localeSpanish => '西班牙语';

  @override
  String get localeFrench => '法国人';

  @override
  String get localeArabic => '巴黎';

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
  String get navLanguage => '语言';

  @override
  String get walletConnected => '已连接';

  @override
  String get walletDisconnect => '断开';

  @override
  String get walletSignInTitle => '使用钱包登录';

  @override
  String get walletSignInBody => '连接与 WalletConnect 兼容的 Algorand 钱包以提交建议和点赞。';

  @override
  String get walletConnect => '连接钱包';

  @override
  String get walletConnectFailed => '连接失败。请重试或先取消配对对话框。';

  @override
  String get walletDialogTitle => '连接你的钱包';

  @override
  String get walletDialogBody =>
      '使用与 WalletConnect 兼容的 Algorand 钱包（Pera、Defly 等）扫描二维码，或在手机上复制/打开链接。';

  @override
  String get walletAwaitingApproval => '钱包已连接。切换回您的钱包应用程序以批准登录请求，然后返回此处。';

  @override
  String get walletAwaitingApprovalTitle => '快到了';

  @override
  String get walletCopyUri => '复制 URI';

  @override
  String get walletUriCopied => 'WalletConnect URI 已复制';

  @override
  String get walletOpenWallet => '打开钱包';

  @override
  String get walletOpenFailed => '无法打开钱包应用程序 — 复制 URI 并将其粘贴到 Pera 或 Defly 中';

  @override
  String get walletCancel => '取消';

  @override
  String get walletDone => '完毕';

  @override
  String get walletErrorTitle => '登录失败';

  @override
  String get walletErrorTimeout => '登录请求已超时。请打开钱包应用后重试。';

  @override
  String get walletErrorRejected => '请求已在钱包中被拒绝。';

  @override
  String get walletErrorGeneric => '无法完成登录。请检查网络连接后重试。';

  @override
  String get walletRetry => '重试';

  @override
  String get walletShowQr => '显示二维码';

  @override
  String get walletMobileHint => '点击下方按钮打开您的 Algorand 钱包，批准连接后返回此页面。';

  @override
  String get walletSignExplainer =>
      '您的钱包会请求签署一条登录消息（旧版钱包会改为显示一笔 0 ALGO 交易）。签名免费且不会向网络提交任何内容——仅用于证明地址归您所有。';

  @override
  String get newsFeedTitle => '最新文章';

  @override
  String get newsSubtitleDefault => '当内容发生变化时，链上触发器和爬网源会在此处发布。';

  @override
  String get articlePublicationDetailsHint => '出版商、日期和来源链接';

  @override
  String get newsPriceUnavailable => 'ALGO 价格不可用 — 运行价格指标收集';

  @override
  String newsArticleCount(int count) {
    return '$count articles in the curated feed';
  }

  @override
  String get newsEmptyFilteredTitle => '还没有文章';

  @override
  String get newsEmptyTitle => '饲料已空';

  @override
  String get newsSponsoredLabel => '赞助';

  @override
  String newsSponsoredBy(String sponsor) {
    return 'Sponsored · $sponsor';
  }

  @override
  String get newsEmptyFilteredMessage => '该来源尚未发表任何文章。检查工作人员是否正在投票以及内容是否已更改。';

  @override
  String get newsEmptyMessage =>
      '启动 Conduit、Celery 工作线程和种子 service_registry 来填充 feed。';

  @override
  String newsFilterShowing(String serviceId) {
    return 'Showing articles from $serviceId';
  }

  @override
  String get clearFilter => '清除';

  @override
  String get articleUntitled => '无题';

  @override
  String get articleSectionTitle => '文章';

  @override
  String get articlePublicationDetails => '出版详情';

  @override
  String get articleMetaService => '服务';

  @override
  String get articleMetaPublisher => '出版商';

  @override
  String get articleMetaDataSource => '数据来源';

  @override
  String get articleMetaCoinGecko => 'CoinGecko 市场数据';

  @override
  String get articleMetaPublished => '已发表';

  @override
  String get articleMetaRound => '圆形的';

  @override
  String get articleMetaTriggerTx => '触发发送';

  @override
  String get articleMetaSourceUrl => '来源网址';

  @override
  String get articleOpenInBrowser => '在浏览器中打开';

  @override
  String get articleViewOnExplorer => '在 Algorand 浏览器上查看';

  @override
  String articleMoreFrom(String serviceId) {
    return 'More from $serviceId';
  }

  @override
  String get articleViewSource => '查看源码';

  @override
  String get adminTitle => '源头管理';

  @override
  String get adminSubtitle => '注册的爬虫和提要（仅对管理员钱包可见）。';

  @override
  String get adminViewFeedArticles => '查看此来源的提要';

  @override
  String get adminAccessDenied => '连接管理员钱包来管理来源。在构建时设置 ADMIN_WALLET_ADDRESSES。';

  @override
  String get sourcesTitle => '新闻来源';

  @override
  String get sourcesSubtitle =>
      '由工作人员抓取的注册服务。 Discord 和 Reddit 按计划进行投票；当监控内容发生变化时，链上匹配触发发布。';

  @override
  String get sourcesEmptyTitle => '未配置源';

  @override
  String get sourcesEmptyMessage =>
      '使用 Discord 和 Reddit TOML 文件为 service_registry 提供种子，然后运行迁移。在 Cassandra 中注册后，来源就会出现在此处。';

  @override
  String get sourcesMetaServiceId => '服务编号';

  @override
  String get sourcesMetaScrapeUrl => '抓取网址';

  @override
  String get sourcesMetaMatchRule => '比赛规则';

  @override
  String get sourcesViewArticles => '查看文章';

  @override
  String get sourcesDisabled => '残疾人';

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
  String get sourceKindDiscord => '不和谐';

  @override
  String get sourceKindReddit => '红迪网';

  @override
  String get sourceKindWeb => '网络';

  @override
  String get sourceKindOnChain => '链上';

  @override
  String get sourceKindUnknown => '来源';

  @override
  String get navContact => '接触';

  @override
  String get contactTitle => '接触';

  @override
  String get contactSubtitle => '更正、提示或反馈——写信给新闻编辑室。';

  @override
  String get contactNameLabel => '你的名字（可选）';

  @override
  String get contactEmailLabel => '电子邮件（可选，如果您需要回复）';

  @override
  String get contactMessageLabel => '信息';

  @override
  String get contactMessageHint => '更正、提示、反馈……';

  @override
  String get contactSend => '发送消息';

  @override
  String get contactSent => '消息已发送 — 感谢您给我们写信。';

  @override
  String get contactTooShort => '请多写几个字（至少10个字符）。';

  @override
  String get searchTitle => '搜索';

  @override
  String get searchSubtitle => '搜索我们发表的每一篇文章。';

  @override
  String get searchQueryLabel => '搜索查询';

  @override
  String get searchQueryHint => '关键词、标题、摘要……';

  @override
  String get searchAction => '搜索';

  @override
  String searchEngine(String name) {
    return 'Engine: $name';
  }

  @override
  String get searchEmptyTitle => '没有结果';

  @override
  String get searchEmptyMessage => '尝试不同的关键字或检查文章是否已编入索引。';

  @override
  String get searchErrorBackend => '暂时无法搜索。检查 API 和数据库是否正在运行。';

  @override
  String get suggestionsTitle => '建议';

  @override
  String get suggestionsSubtitle => '向平台金库发送至少 0.01 ALGO 后提交。支持投票使用链下钱包签名。';

  @override
  String get suggestionsNewTitle => '新建议';

  @override
  String get suggestionsFieldTitle => '标题';

  @override
  String get suggestionsFieldBody => '身体';

  @override
  String get suggestionsFieldTxid => '提交交易ID';

  @override
  String get suggestionsSubmit => '提交建议';

  @override
  String get suggestionsUpvoteTitle => '点赞';

  @override
  String get suggestionsSignatureLabel => '签名（base64）';

  @override
  String get suggestionsSignatureHint => '在钱包中签署准备好的消息后';

  @override
  String get suggestionsSubmitUpvote => '提交赞成票';

  @override
  String get suggestionsPrepareUpvote => '准备投票';

  @override
  String get suggestionsUpvoteDialogTitle => '报名投票留言';

  @override
  String get suggestionsCopyMessage => '复制消息';

  @override
  String get suggestionsMessageCopied => '签名消息已复制到剪贴板';

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
  String get close => '关闭';

  @override
  String suggestionsTxShort(String txid) {
    return 'Tx $txid';
  }

  @override
  String get snackConnectWallet => '首先连接你的钱包';

  @override
  String get snackSuggestionSubmitted => '已提交建议';

  @override
  String get snackUpvoteRecorded => '已记录赞成票';

  @override
  String get snackChooseSuggestionUpvote => '选择一个建议先点赞';

  @override
  String metaPublishedEpoch(String epoch) {
    return 'Published: $epoch';
  }

  @override
  String metaPublishedRelative(String when) {
    return 'Published: $when';
  }

  @override
  String get timeJustNow => '现在';

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
  String get navFrontPage => '头版';

  @override
  String get navLatest => '最新的';

  @override
  String get navSections => '部分';

  @override
  String get navAbout => '关于';

  @override
  String get navApps => '应用程序';

  @override
  String get navProductsMenuHint => '探索平台';

  @override
  String get frontPageTopStories => '热门故事';

  @override
  String get frontPageLatest => '最新的';

  @override
  String get frontPageMore => '更多来自新闻编辑室';

  @override
  String frontPageSectionStories(String section) {
    return 'More in $section';
  }

  @override
  String get sectionMarkets => '市场';

  @override
  String get sectionSecurity => '安全';

  @override
  String get sectionDevelopers => '开发商';

  @override
  String get sectionCommunity => '社区';

  @override
  String get sectionEcosystem => '生态系统';

  @override
  String get sectionEmptyTitle => '这里还什么都没有';

  @override
  String get sectionEmptyMessage => '本节尚未归档任何故事。请尽快回来查看。';

  @override
  String get bylineNewsroom => '新闻编辑室';

  @override
  String get bylineMarketsDesk => '市场服务台';

  @override
  String get bylineChainDesk => '链上服务台';

  @override
  String articleByline(String desk) {
    return 'By $desk';
  }

  @override
  String articleReadingTime(int count) {
    return '$count min read';
  }

  @override
  String get articleRelatedTitle => '相关故事';

  @override
  String get articleShare => '分享';

  @override
  String get articleLinkCopied => '链接已复制到剪贴板';

  @override
  String get footerTagline => '独立、自动化地覆盖 Algorand 生态系统。';

  @override
  String get footerSectionsHeading => '部分';

  @override
  String get footerAboutHeading => '关于';

  @override
  String footerRights(String year) {
    return '© $year PXke Algorand. Independent coverage of the Algorand ecosystem.';
  }

  @override
  String get aboutTitle => '关于 PXke Algorand';

  @override
  String get aboutLead =>
      'PXke Algorand 是一家独立的新闻编辑室，涵盖 Algorand 区块链——其市场、协议、治理以及基于其的项目。';

  @override
  String get aboutHowHeading => '我们如何发布';

  @override
  String get aboutHowBody =>
      '我们的报道是根据链上事件、预定市场数据和监控的社区来源自动组装的，然后通过我们的编辑渠道组织成多个部分。每个故事都有出处，因此您可以追踪它的来源。';

  @override
  String get aboutAiHeading => '用人工智能编写';

  @override
  String get aboutAiBody =>
      'PXke Algorand 出版人工智能辅助新闻。我们的文章是由人工智能语言模型根据上述链上事件、市场数据和社区来源起草的，并经过自动编辑审查——它们是机器生成的，而不是由人类记者撰写的。我们链接到每个故事的原始来源，以便您可以验证每个声明，并且组织（而不是个人署名）是记录的作者。';

  @override
  String get aboutProvenanceHeading => '来源和透明度';

  @override
  String get aboutProvenanceBody =>
      '链上故事由可验证的交易触发，并链接回 Algorand 浏览器。市场故事借鉴了 CoinGecko 数据。赞助广告位始终贴有这样的标签，并与社论明确分开。';

  @override
  String get aboutStandardsHeading => '编辑标准';

  @override
  String get aboutStandardsBody =>
      '我们的目标是准确性和清晰的归属。本出版物不构成投资建议。发现错误了吗？通过任何故事的来源链接进行联系。';

  @override
  String get seedsTitle => '种子';

  @override
  String get seedsSubtitle => '手动配置爬虫的起点。通过发现批准的域位于“域”选项卡中。';

  @override
  String get sourcesAdd => '添加来源';

  @override
  String get sourcesEdit => '编辑';

  @override
  String get sourcesEditTitle => '编辑来源';

  @override
  String get sourcesAddTitle => '添加来源';

  @override
  String get sourcesDeleteTitle => '删除源吗？';

  @override
  String sourcesDeleteBody(String serviceId) {
    return '\"$serviceId\" will stop being crawled. Articles already published stay.';
  }

  @override
  String get sourcesDelete => '删除';

  @override
  String get sourcesSave => '节省';

  @override
  String get sourcesAddAction => '添加';

  @override
  String get sourcesRequiredFields => 'Service id, name and URL are required.';

  @override
  String get sourcesChangesNextPoll => '更改将应用​​于下一次爬网程序轮询。';

  @override
  String get sourcesMerge => '合并';

  @override
  String get sourcesMergeTitle => '合并服务';

  @override
  String get sourcesMergeIntro =>
      '将多项服务合二为一。 The chosen services\' sources and domains move to the target; the emptied services are disabled.当一种产品跨越多个域（例如 algorand.co + algorand.com）时使用此选项。';

  @override
  String get sourcesMergeTarget => '保持为目标';

  @override
  String get sourcesMergeFold => '折叠（将被禁用）';

  @override
  String get sourcesMergeAction => '合并';

  @override
  String get sourcesMergeNeedsTwo => '选择一个目标和至少一项要纳入的服务。';

  @override
  String sourcesMergeDone(int count, String target) {
    return 'Merged $count service(s) into $target.';
  }

  @override
  String get sourcesFieldServiceId => '服务编号';

  @override
  String get sourcesFieldServiceIdHint => '烤肉串盒，例如algorand 基金会博客';

  @override
  String get sourcesFieldDisplayName => '显示名称';

  @override
  String get sourcesFieldScrapeUrl => '抓取网址';

  @override
  String get sourcesFieldScrapeUrlHint =>
      'https://…、reddit://r/…、discord://channel/…';

  @override
  String get sourcesFieldMatchKind => '匹配种类';

  @override
  String get sourcesFieldMatchKindHint => '地址 / app_id / asset_id';

  @override
  String get sourcesFieldMatchValue => '匹配值';

  @override
  String get sourcesFieldMatchValueHint => '例如钱包地址或应用程序ID';

  @override
  String get sourcesMatchRuleHelp =>
      '匹配规则将此源链接到链上活动：当链爬虫看到发送者/接收者地址（种类“address”）、应用程序 ID（“app_id”）或资产 id（“asset_id”）等于匹配值的主网交易时，它将事件归因于该源并可以触发文章。对于网络或 Reddit 来源，它纯粹是信息性的 - 使用诸如域名或 subreddit 之类的描述性内容。';

  @override
  String get sourcesEnabled => '启用';

  @override
  String get actionCancel => '取消';

  @override
  String get actionRefresh => '刷新';

  @override
  String get adminTabSeeds => '种子';

  @override
  String get adminTabArticles => '文章';

  @override
  String get adminTabWriterBriefs => '作家简介';

  @override
  String get adminTabClassifier => '分类器';

  @override
  String get adminTabDomains => '域名';

  @override
  String get adminTabToolInsights => '工具见解';

  @override
  String get adminTabSessions => '会议';

  @override
  String get adminTabSystem => '系统';

  @override
  String get domainsIntro =>
      '爬行前沿：链接爬行器所遇到的域。永远不会探索死胡同 - 通过相关性评分和您的评论判断自动设置，或在此处手动设置。';

  @override
  String get domainsFilterAll => '全部';

  @override
  String domainsFilterAllCount(int count) {
    return 'All ($count)';
  }

  @override
  String get domainsFilterPending => '待审核';

  @override
  String domainsFilterPendingCount(int count) {
    return 'Pending review ($count)';
  }

  @override
  String get domainsFilterDeadEnds => '死胡同';

  @override
  String domainsFilterDeadEndsCount(int count) {
    return 'Dead ends ($count)';
  }

  @override
  String get domainsEmptyTitle => '还没有域名';

  @override
  String get domainsEmptyMessage => '爬虫会记录在跟踪链接时遇到的每个域。';

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
  String get domainsDeadEnd => '死胡同';

  @override
  String get domainsApproveExplore => '批准并探索';

  @override
  String get domainsMarkDeadEnd => '标记死胡同';

  @override
  String get domainsRevive => '复活';

  @override
  String domainsApprovedSnack(String domain) {
    return '$domain approved — the crawler may explore it';
  }

  @override
  String domainsDeadEndSnack(String domain) {
    return '$domain marked as dead end';
  }

  @override
  String get domainsWalletNotConnected => '钱包未连接';
}
