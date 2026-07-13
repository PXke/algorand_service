// ignore: unused_import
import 'package:intl/intl.dart' as intl;
import 'app_localizations.dart';

// ignore_for_file: type=lint

/// The translations for Spanish Castilian (`es`).
class AppLocalizationsEs extends AppLocalizations {
  AppLocalizationsEs([String locale = 'es']) : super(locale);

  @override
  String get appTitle => 'PXke Algorand Projects';

  @override
  String get appTagline => 'Noticias, fuentes y comunidad';

  @override
  String get navHome => 'Inicio';

  @override
  String get navNews => 'Noticias';

  @override
  String get navSources => 'Fuentes';

  @override
  String get navSuggestions => 'Sugerencias';

  @override
  String get navSearch => 'Buscar';

  @override
  String get navAdmin => 'Administración';

  @override
  String get navProducts => 'PRODUCTOS';

  @override
  String get navWallet => 'Cartera';

  @override
  String get navAppearance => 'APARIENCIA';

  @override
  String get pageTitleHome => 'PXke Algorand Projects';

  @override
  String get pageTitleArticle => 'Artículo';

  @override
  String get pageTitleNews => 'Noticias';

  @override
  String get pageTitleSources => 'Fuentes de noticias';

  @override
  String get pageTitleSuggestions => 'Sugerencias';

  @override
  String get pageTitleSearch => 'Buscar';

  @override
  String get pageTitleAdmin => 'Administración';

  @override
  String get backToFeed => 'Volver al feed';

  @override
  String get homeWelcome => 'Plataforma del ecosistema PXke Algorand';

  @override
  String get homeTagline =>
      'Noticias curadas de disparadores on-chain y fuentes rastreadas, sugerencias comunitarias y búsqueda.';

  @override
  String get homeNewsDescription =>
      'Filtrado de artículos publicados cuando las fuentes monitoreadas o eventos en la cadena cambian.';

  @override
  String get homeSourcesDescription =>
      'Crawlers registrados de Discord, Reddit y web que alimentan el pipeline de noticias.';

  @override
  String get homeSuggestionsDescription =>
      'Envía y vota ideas de la comunidad con autenticación respaldada por cartera.';

  @override
  String get homeSearchDescription =>
      'Búsqueda de texto completo en los artículos publicados.';

  @override
  String get homeOpenProduct => 'Abrir';

  @override
  String get themeLight => 'Claro';

  @override
  String get themeDark => 'Oscuro';

  @override
  String get themeSystem => 'Sistema';

  @override
  String get themeSwitchToLight => 'Cambiar a tema claro';

  @override
  String get themeSwitchToDark => 'Cambiar a tema oscuro';

  @override
  String get localeSystem => 'Idioma del sistema';

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
  String get navLanguage => 'IDIOMA';

  @override
  String get walletConnected => 'Conectado';

  @override
  String get walletDisconnect => 'Desconectar';

  @override
  String get walletSignInTitle => 'Iniciar sesión con cartera';

  @override
  String get walletSignInBody =>
      'Conecta una cartera Algorand compatible con WalletConnect para enviar sugerencias y votos.';

  @override
  String get walletConnect => 'Conectar cartera';

  @override
  String get walletConnectFailed =>
      'Error de conexión. Inténtalo de nuevo o cancela el emparejamiento primero.';

  @override
  String get walletDialogTitle => 'Conectar tu cartera';

  @override
  String get walletDialogBody =>
      'Escanea el código QR con una cartera compatible (Pera, Defly, etc.) o copia/abre el enlace en el móvil.';

  @override
  String get walletAwaitingApproval =>
      'Monedero conectado. Vuelve a tu aplicación de monedero para aprobar la solicitud de inicio de sesión y regresa aquí.';

  @override
  String get walletAwaitingApprovalTitle => 'Casi listo';

  @override
  String get walletCopyUri => 'Copiar URI';

  @override
  String get walletUriCopied => 'URI de WalletConnect copiado';

  @override
  String get walletOpenWallet => 'Abrir cartera';

  @override
  String get walletOpenFailed =>
      'No se pudo abrir la cartera — copia el URI y pégalo en Pera o Defly';

  @override
  String get walletCancel => 'Cancelar';

  @override
  String get walletDone => 'Listo';

  @override
  String get walletErrorTitle => 'Error al iniciar sesión';

  @override
  String get walletErrorTimeout =>
      'La solicitud de inicio de sesión expiró. Abre tu cartera e inténtalo de nuevo.';

  @override
  String get walletErrorRejected => 'La solicitud fue rechazada en la cartera.';

  @override
  String get walletErrorGeneric =>
      'No se pudo completar el inicio de sesión. Comprueba tu conexión e inténtalo de nuevo.';

  @override
  String get walletRetry => 'Reintentar';

  @override
  String get walletShowQr => 'Mostrar código QR';

  @override
  String get walletMobileHint =>
      'Toca el botón de abajo para abrir tu cartera de Algorand, aprueba la conexión y vuelve aquí.';

  @override
  String get walletSignExplainer =>
      'Tu cartera te pedirá firmar un mensaje de inicio de sesión (las carteras antiguas muestran una transacción de 0 ALGO). Firmar es gratis y nada se envía a la red: solo demuestra que la dirección es tuya.';

  @override
  String get newsFeedTitle => 'Últimos artículos';

  @override
  String get newsSubtitleDefault =>
      'Los disparadores on-chain y las fuentes rastreadas publican aquí cuando cambia el contenido.';

  @override
  String get articlePublicationDetailsHint =>
      'Editor, fecha y enlace de origen';

  @override
  String get newsPriceUnavailable =>
      'Precio de ALGO no disponible — ejecuta la recolección de métricas';

  @override
  String newsArticleCount(int count) {
    return '$count artículos en el feed curado';
  }

  @override
  String get newsEmptyFilteredTitle => 'Aún no hay artículos';

  @override
  String get newsEmptyTitle => 'El feed está vacío';

  @override
  String get newsSponsoredLabel => 'Patrocinado';

  @override
  String newsSponsoredBy(String sponsor) {
    return 'Patrocinado · $sponsor';
  }

  @override
  String get newsEmptyFilteredMessage =>
      'Esta fuente no ha publicado artículos. Comprueba que los workers estén activos y el contenido haya cambiado.';

  @override
  String get newsEmptyMessage =>
      'Inicia Conduit, workers Celery y registra service_registry para poblar el feed.';

  @override
  String newsFilterShowing(String serviceId) {
    return 'Mostrando artículos de $serviceId';
  }

  @override
  String get clearFilter => 'Quitar';

  @override
  String get articleUntitled => 'Sin título';

  @override
  String get articleSectionTitle => 'Artículo';

  @override
  String get articlePublicationDetails => 'Detalles de publicación';

  @override
  String get articleMetaService => 'Servicio';

  @override
  String get articleMetaPublisher => 'Editor';

  @override
  String get articleMetaDataSource => 'Fuente de datos';

  @override
  String get articleMetaCoinGecko => 'Datos de mercado de CoinGecko';

  @override
  String get articleMetaPublished => 'Publicado';

  @override
  String get articleMetaRound => 'Ronda';

  @override
  String get articleMetaTriggerTx => 'Tx disparador';

  @override
  String get articleMetaSourceUrl => 'URL de origen';

  @override
  String get articleOpenInBrowser => 'Abrir en el navegador';

  @override
  String get articleViewOnExplorer => 'Ver en el explorador de Algorand';

  @override
  String articleMoreFrom(String serviceId) {
    return 'Más de $serviceId';
  }

  @override
  String get articleViewSource => 'Ver fuente';

  @override
  String get adminTitle => 'Administración de fuentes';

  @override
  String get adminSubtitle =>
      'Crawlers y feeds registrados (visibles solo para carteras de administrador).';

  @override
  String get adminViewFeedArticles => 'Ver feed para esta fuente';

  @override
  String get adminAccessDenied =>
      'Conecta una cartera de administrador para gestionar fuentes. Establece ADMIN_WALLET_ADDRESSES en el momento de la compilación.';

  @override
  String get sourcesTitle => 'Fuentes de noticias';

  @override
  String get sourcesSubtitle =>
      'Servicios registrados rastreados por workers. Discord y Reddit se consultan periódicamente; las coincidencias on-chain publican cuando cambia el contenido monitoreado.';

  @override
  String get sourcesEmptyTitle => 'No hay fuentes configuradas';

  @override
  String get sourcesEmptyMessage =>
      'Registra service_registry con archivos TOML de Discord y Reddit, luego ejecuta migraciones. Las fuentes aparecerán aquí una vez registradas en Cassandra.';

  @override
  String get sourcesMetaServiceId => 'ID de servicio';

  @override
  String get sourcesMetaScrapeUrl => 'URL de rastreo';

  @override
  String get sourcesMetaMatchRule => 'Regla de coincidencia';

  @override
  String get sourcesViewArticles => 'Ver artículos';

  @override
  String get sourcesDisabled => 'Desactivado';

  @override
  String filterAll(int count) {
    return 'Todos ($count)';
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
  String get sourceKindUnknown => 'Fuente';

  @override
  String get navContact => 'Contacto';

  @override
  String get contactTitle => 'Contacto';

  @override
  String get contactSubtitle =>
      'Correcciones, pistas o comentarios: escribe a la redacción.';

  @override
  String get contactNameLabel => 'Tu nombre (opcional)';

  @override
  String get contactEmailLabel =>
      'Correo electrónico (opcional, si quieres respuesta)';

  @override
  String get contactMessageLabel => 'Mensaje';

  @override
  String get contactMessageHint => 'Correcciones, pistas, comentarios…';

  @override
  String get contactSend => 'Enviar mensaje';

  @override
  String get contactSent => 'Mensaje enviado. Gracias por escribirnos.';

  @override
  String get contactTooShort => 'Escribe algo más (al menos 10 caracteres).';

  @override
  String get searchTitle => 'Buscar';

  @override
  String get searchSubtitle =>
      'Busca entre todos nuestros artículos publicados.';

  @override
  String get searchQueryLabel => 'Consulta';

  @override
  String get searchQueryHint => 'Palabras clave, títulos, resúmenes…';

  @override
  String get searchAction => 'Buscar';

  @override
  String searchEngine(String name) {
    return 'Motor: $name';
  }

  @override
  String get searchEmptyTitle => 'Sin resultados';

  @override
  String get searchEmptyMessage =>
      'Prueba diferentes palabras clave o verifica que los artículos estén indexados.';

  @override
  String get searchErrorBackend =>
      'La búsqueda no está disponible temporalmente. Verifica que la API y la base de datos estén en ejecución.';

  @override
  String get suggestionsTitle => 'Sugerencias';

  @override
  String get suggestionsSubtitle =>
      'Envía después de transferir al menos 0.01 ALGO a la tesorería de la plataforma. Los votos usan una firma off-chain de la cartera.';

  @override
  String get suggestionsNewTitle => 'Nueva sugerencia';

  @override
  String get suggestionsFieldTitle => 'Título';

  @override
  String get suggestionsFieldBody => 'Cuerpo';

  @override
  String get suggestionsFieldTxid => 'ID de transacción de envío';

  @override
  String get suggestionsSubmit => 'Enviar sugerencia';

  @override
  String get suggestionsUpvoteTitle => 'Votar';

  @override
  String get suggestionsSignatureLabel => 'Firma (base64)';

  @override
  String get suggestionsSignatureHint =>
      'Después de firmar el mensaje preparado en tu cartera';

  @override
  String get suggestionsSubmitUpvote => 'Enviar voto';

  @override
  String get suggestionsPrepareUpvote => 'Preparar voto';

  @override
  String get suggestionsUpvoteDialogTitle => 'Firmar mensaje de voto';

  @override
  String get suggestionsCopyMessage => 'Copiar mensaje';

  @override
  String get suggestionsMessageCopied =>
      'Mensaje de firma copiado al portapapeles';

  @override
  String suggestionsTreasuryHelp(String minAlgo, String address) {
    return 'Envía al menos $minAlgo ALGO a la tesorería de la plataforma antes de enviar:\n$address';
  }

  @override
  String suggestionsUpvoteCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count votos',
      one: '1 voto',
      zero: 'Sin votos aún',
    );
    return '$_temp0';
  }

  @override
  String get close => 'Cerrar';

  @override
  String suggestionsTxShort(String txid) {
    return 'Tx $txid';
  }

  @override
  String get snackConnectWallet => 'Conecta tu cartera primero';

  @override
  String get snackSuggestionSubmitted => 'Sugerencia enviada';

  @override
  String get snackUpvoteRecorded => 'Voto registrado';

  @override
  String get snackChooseSuggestionUpvote =>
      'Elige una sugerencia para votar primero';

  @override
  String metaPublishedEpoch(String epoch) {
    return 'Publicado: $epoch';
  }

  @override
  String metaPublishedRelative(String when) {
    return 'Publicado: $when';
  }

  @override
  String get timeJustNow => 'justo ahora';

  @override
  String timeMinutesAgo(int count) {
    return 'hace $count min';
  }

  @override
  String timeHoursAgo(int count) {
    return 'hace $count h';
  }

  @override
  String timeDaysAgo(int count) {
    return 'hace $count d';
  }

  @override
  String metaRound(String round) {
    return 'Ronda: $round';
  }

  @override
  String metaService(String serviceId) {
    return 'Servicio: $serviceId';
  }

  @override
  String get navFrontPage => 'Portada';

  @override
  String get navLatest => 'Últimas';

  @override
  String get navSections => 'SECCIONES';

  @override
  String get navAbout => 'Acerca de';

  @override
  String get navApps => 'Apps';

  @override
  String get navProductsMenuHint => 'Explorar la plataforma';

  @override
  String get frontPageTopStories => 'Historias destacadas';

  @override
  String get frontPageLatest => 'Últimas';

  @override
  String get frontPageMore => 'Más de la redacción';

  @override
  String frontPageSectionStories(String section) {
    return 'Más en $section';
  }

  @override
  String get navHot => 'Popular';

  @override
  String get navTopics => 'Temas';

  @override
  String get hotTitle => 'Lo más leído';

  @override
  String get hotLead =>
      'Las historias que los lectores más están abriendo ahora mismo.';

  @override
  String get topicsTitle => 'Temas';

  @override
  String get topicsLead =>
      'Cada etiqueta usada por la redacción recientemente: el tamaño refleja la cobertura y el color, las lecturas.';

  @override
  String topicSubtitle(String tag) {
    return 'Historias con la etiqueta “$tag”';
  }

  @override
  String readsCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count lecturas',
      one: '1 lectura',
    );
    return '$_temp0';
  }

  @override
  String get sectionMarkets => 'Mercados';

  @override
  String get sectionSecurity => 'Seguridad';

  @override
  String get sectionDevelopers => 'Desarrolladores';

  @override
  String get sectionCommunity => 'Comunidad';

  @override
  String get sectionEcosystem => 'Ecosistema';

  @override
  String get sectionEmptyTitle => 'Nada aquí todavía';

  @override
  String get sectionEmptyMessage =>
      'Aún no hay historias en esta sección. Vuelve pronto.';

  @override
  String get bylineNewsroom => 'La Redacción';

  @override
  String get bylineMarketsDesk => 'Redacción de Mercados';

  @override
  String get bylineChainDesk => 'Redacción On-Chain';

  @override
  String articleByline(String desk) {
    return 'Por $desk';
  }

  @override
  String articleReadingTime(int count) {
    return '$count min de lectura';
  }

  @override
  String get articleRelatedTitle => 'Historias relacionadas';

  @override
  String get articleShare => 'Compartir';

  @override
  String get articleShareCopyLink => 'Copiar enlace';

  @override
  String get articleLinkCopied => 'Enlace copiado al portapapeles';

  @override
  String get footerTagline =>
      'Cobertura independiente y automatizada del ecosistema Algorand.';

  @override
  String get footerSectionsHeading => 'Secciones';

  @override
  String get footerAboutHeading => 'Acerca de';

  @override
  String get footerFollowHeading => 'Síguenos';

  @override
  String footerRights(String year) {
    return '© $year PXke Algorand. Cobertura independiente del ecosistema Algorand.';
  }

  @override
  String get aboutTitle => 'Acerca de PXke Algorand';

  @override
  String get aboutLead =>
      'PXke Algorand es una redacción independiente que cubre la blockchain Algorand: mercados, protocolo, gobernanza y los proyectos que se construyen sobre ella.';

  @override
  String get aboutHowHeading => 'Cómo publicamos';

  @override
  String get aboutHowBody =>
      'Nuestra cobertura se ensambla automáticamente a partir de eventos on-chain, datos de mercado programados y fuentes comunitarias monitorizadas, y luego se organiza en secciones. Cada historia incluye procedencia para que puedas rastrear su origen.';

  @override
  String get aboutAiHeading => 'Redactado con IA';

  @override
  String get aboutAiBody =>
      'PXke Algorand publica periodismo asistido por IA. Los artículos se redactan con modelos de lenguaje a partir de eventos on-chain, datos de mercado y fuentes comunitarias, bajo revisión editorial automatizada. Enlazamos las fuentes originales en cada historia.';

  @override
  String get aboutProvenanceHeading => 'Procedencia y transparencia';

  @override
  String get aboutProvenanceBody =>
      'Las historias on-chain se activan por transacciones verificables y enlazan al explorador de Algorand. Las de mercado usan datos de CoinGecko. Los contenidos patrocinados siempre se etiquetan como tales.';

  @override
  String get aboutStandardsHeading => 'Estándares editoriales';

  @override
  String get aboutStandardsBody =>
      'Buscamos precisión y atribución clara. Esta publicación no es asesoramiento de inversión. ¿Viste un error? Escríbenos desde los enlaces de fuente de cualquier historia.';

  @override
  String get seedsTitle => 'Semillas';

  @override
  String get seedsSubtitle =>
      'Puntos de partida configurados manualmente para el rastreador. Los dominios aprobados desde el descubrimiento aparecen en la pestaña Dominios.';

  @override
  String get sourcesAdd => 'Añadir fuente';

  @override
  String get sourcesEdit => 'Editar';

  @override
  String get sourcesEditTitle => 'Editar fuente';

  @override
  String get sourcesAddTitle => 'Añadir fuente';

  @override
  String get sourcesDeleteTitle => '¿Eliminar fuente?';

  @override
  String sourcesDeleteBody(String serviceId) {
    return '«$serviceId» dejará de rastrearse. Los artículos ya publicados se mantienen.';
  }

  @override
  String get sourcesDelete => 'Eliminar';

  @override
  String get sourcesSave => 'Guardar';

  @override
  String get sourcesAddAction => 'Añadir';

  @override
  String get sourcesRequiredFields =>
      'El id de servicio, el nombre y la URL son obligatorios.';

  @override
  String get sourcesChangesNextPoll =>
      'Los cambios se aplican en la siguiente consulta del rastreador.';

  @override
  String get sourcesMerge => 'Fusionar';

  @override
  String get sourcesMergeTitle => 'Fusionar servicios';

  @override
  String get sourcesMergeIntro =>
      'Combina varios servicios en uno. Las fuentes y dominios de los servicios elegidos pasan al objetivo; los vaciados se desactivan.';

  @override
  String get sourcesMergeTarget => 'Conservar como objetivo';

  @override
  String get sourcesMergeFold => 'Incluir (se desactivará)';

  @override
  String get sourcesMergeAction => 'Fusionar';

  @override
  String get sourcesMergeNeedsTwo =>
      'Elige un objetivo y al menos un servicio para incluir.';

  @override
  String sourcesMergeDone(int count, String target) {
    return 'Se fusionaron $count servicio(s) en $target.';
  }

  @override
  String get sourcesFieldServiceId => 'Id de servicio';

  @override
  String get sourcesFieldServiceIdHint =>
      'kebab-case, p. ej. algorand-foundation-blog';

  @override
  String get sourcesFieldDisplayName => 'Nombre visible';

  @override
  String get sourcesFieldScrapeUrl => 'URL de rastreo';

  @override
  String get sourcesFieldScrapeUrlHint =>
      'https://…, reddit://r/…, discord://channel/…';

  @override
  String get sourcesFieldMatchKind => 'Tipo de coincidencia';

  @override
  String get sourcesFieldMatchKindHint => 'address / app_id / asset_id';

  @override
  String get sourcesFieldMatchValue => 'Valor de coincidencia';

  @override
  String get sourcesFieldMatchValueHint =>
      'p. ej. dirección de cartera o id de app';

  @override
  String get sourcesMatchRuleHelp =>
      'La regla de coincidencia vincula esta fuente con la actividad on-chain: cuando el rastreador de cadena ve una transacción en MainNet cuya dirección emisora/receptora (tipo «address»), id de aplicación («app_id») o id de activo («asset_id») coincide con el valor, atribuye el evento a esta fuente y puede publicar un artículo. Para fuentes web o Reddit es solo informativo — usa algo descriptivo como el dominio o el subreddit.';

  @override
  String get sourcesEnabled => 'Activado';

  @override
  String get actionCancel => 'Cancelar';

  @override
  String get actionRefresh => 'Actualizar';

  @override
  String get adminTabSeeds => 'Semillas';

  @override
  String get adminTabArticles => 'Artículos';

  @override
  String get adminTabWriterBriefs => 'Briefs del redactor';

  @override
  String get adminTabClassifier => 'Clasificador';

  @override
  String get adminTabDomains => 'Dominios';

  @override
  String get adminTabToolInsights => 'Herramientas';

  @override
  String get adminTabSessions => 'Sesiones';

  @override
  String get adminTabSystem => 'Sistema';

  @override
  String get domainsIntro =>
      'Frontera de rastreo: dominios que el rastreador de enlaces ha encontrado. Los callejones sin salida nunca se exploran — se marcan automáticamente por relevancia y tus revisiones, o manualmente aquí.';

  @override
  String get domainsFilterAll => 'Todos';

  @override
  String domainsFilterAllCount(int count) {
    return 'Todos ($count)';
  }

  @override
  String get domainsFilterPending => 'Pendientes de revisión';

  @override
  String domainsFilterPendingCount(int count) {
    return 'Pendientes de revisión ($count)';
  }

  @override
  String get domainsFilterDeadEnds => 'Callejones sin salida';

  @override
  String domainsFilterDeadEndsCount(int count) {
    return 'Callejones sin salida ($count)';
  }

  @override
  String get domainsEmptyTitle => 'Aún no hay dominios';

  @override
  String get domainsEmptyMessage =>
      'El rastreador registra cada dominio que encuentra al seguir enlaces.';

  @override
  String domainsOpenInNewTab(String domain) {
    return 'Abrir $domain en una pestaña nueva';
  }

  @override
  String domainsKeywords(String keywords) {
    return 'palabras clave: $keywords';
  }

  @override
  String domainsLinkedAs(String text) {
    return 'enlazado como «$text»';
  }

  @override
  String domainsPredictedInterest(String score) {
    return 'interés previsto $score';
  }

  @override
  String domainsFoundOn(String url) {
    return 'encontrado en $url';
  }

  @override
  String domainsScore(String score) {
    return 'puntuación $score';
  }

  @override
  String domainsCrawled(String date) {
    return 'rastreado $date';
  }

  @override
  String domainsPagesCrawled(int count) {
    return '$count páginas';
  }

  @override
  String get domainsDeadEnd => 'Callejón sin salida';

  @override
  String get domainsApproveExplore => 'Aprobar y explorar';

  @override
  String get domainsCrawlOnce => 'Rastrear una vez, sin fuente';

  @override
  String get domainsAddButton => 'Añadir';

  @override
  String get domainsAddAsSeed => 'Añadir como fuente permanente';

  @override
  String get domainsScoreUnexplained => 'Desglose de puntuación no disponible';

  @override
  String get paginationPrevious => 'Anterior';

  @override
  String get paginationNext => 'Siguiente';

  @override
  String paginationPageOf(int page, int total) {
    return 'Página $page de $total';
  }

  @override
  String get domainsMarkDeadEnd => 'Marcar como callejón sin salida';

  @override
  String get domainsRevive => 'Reactivar';

  @override
  String domainsApprovedSnack(String domain) {
    return '$domain aprobado — el rastreador puede explorarlo';
  }

  @override
  String domainsDeadEndSnack(String domain) {
    return '$domain marcado como callejón sin salida';
  }

  @override
  String get domainsWalletNotConnected => 'Cartera no conectada';

  @override
  String get frontPageMoreNews => 'Más noticias';

  @override
  String storiesCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count historias',
      one: '1 historia',
    );
    return '$_temp0';
  }

  @override
  String get byTheNumbersRange => 'Últimos 7 días';

  @override
  String get byTheNumbersMarketCap => 'Capitalización';

  @override
  String get byTheNumbersVolume => 'Volumen 24h';

  @override
  String get hotTabHot => 'Tendencia ahora';

  @override
  String get hotTabAllTime => 'Histórico';
}
