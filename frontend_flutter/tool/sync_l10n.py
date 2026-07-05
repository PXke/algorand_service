#!/usr/bin/env python3
"""Fill missing ARB keys from app_en.arb and machine-translate gaps.

Keeps existing translations; only adds or updates keys listed in PATCHES or
keys missing from a locale file. Run from repo root:

  pip install deep-translator
  python frontend_flutter/tool/sync_l10n.py
"""

from __future__ import annotations

import json
import re
import time
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
L10N = ROOT / "lib" / "l10n"
EN_PATH = L10N / "app_en.arb"
TARGETS = ("ar", "es", "fr", "zh", "hi")

# Curated patches for keys that were missing from older locale files.
PATCHES: dict[str, dict[str, str]] = {
    "es": {
        "pageTitleAdmin": "Administración",
        "pageTitleNews": "Noticias",
        "walletCancel": "Cancelar",
        "walletConnectFailed": "Error de conexión. Inténtalo de nuevo o cancela el emparejamiento primero.",
        "walletOpenFailed": "No se pudo abrir la cartera — copia el URI y pégalo en Pera o Defly",
        "walletUriCopied": "URI de WalletConnect copiado",
        "aboutTitle": "Acerca de PXke Algorand",
        "aboutLead": "PXke Algorand es una redacción independiente que cubre la blockchain Algorand: mercados, protocolo, gobernanza y los proyectos que se construyen sobre ella.",
        "aboutHowHeading": "Cómo publicamos",
        "aboutHowBody": "Nuestra cobertura se ensambla automáticamente a partir de eventos on-chain, datos de mercado programados y fuentes comunitarias monitorizadas, y luego se organiza en secciones. Cada historia incluye procedencia para que puedas rastrear su origen.",
        "aboutAiHeading": "Redactado con IA",
        "aboutAiBody": "PXke Algorand publica periodismo asistido por IA. Los artículos se redactan con modelos de lenguaje a partir de eventos on-chain, datos de mercado y fuentes comunitarias, bajo revisión editorial automatizada. Enlazamos las fuentes originales en cada historia.",
        "aboutProvenanceHeading": "Procedencia y transparencia",
        "aboutProvenanceBody": "Las historias on-chain se activan por transacciones verificables y enlazan al explorador de Algorand. Las de mercado usan datos de CoinGecko. Los contenidos patrocinados siempre se etiquetan como tales.",
        "aboutStandardsHeading": "Estándares editoriales",
        "aboutStandardsBody": "Buscamos precisión y atribución clara. Esta publicación no es asesoramiento de inversión. ¿Viste un error? Escríbenos desde los enlaces de fuente de cualquier historia.",
        "articleByline": "Por {desk}",
        "articleLinkCopied": "Enlace copiado al portapapeles",
        "articlePublicationDetailsHint": "Editor, fecha y enlace de origen",
        "articleReadingTime": "{count} min de lectura",
        "articleRelatedTitle": "Historias relacionadas",
        "articleShare": "Compartir",
        "bylineChainDesk": "Redacción On-Chain",
        "bylineMarketsDesk": "Redacción de Mercados",
        "bylineNewsroom": "La Redacción",
        "footerAboutHeading": "Acerca de",
        "footerRights": "© {year} PXke Algorand. Cobertura independiente del ecosistema Algorand.",
        "footerSectionsHeading": "Secciones",
        "footerTagline": "Cobertura independiente y automatizada del ecosistema Algorand.",
        "frontPageLatest": "Últimas",
        "frontPageMore": "Más de la redacción",
        "frontPageSectionStories": "Más en {section}",
        "frontPageTopStories": "Historias destacadas",
        "navAbout": "Acerca de",
        "navApps": "Apps",
        "navFrontPage": "Portada",
        "navLatest": "Últimas",
        "navProductsMenuHint": "Explorar la plataforma",
        "navSections": "SECCIONES",
        "sectionCommunity": "Comunidad",
        "sectionDevelopers": "Desarrolladores",
        "sectionEcosystem": "Ecosistema",
        "sectionEmptyMessage": "Aún no hay historias en esta sección. Vuelve pronto.",
        "sectionEmptyTitle": "Nada aquí todavía",
        "sectionMarkets": "Mercados",
        "sectionSecurity": "Seguridad",
        "sourcesMerge": "Fusionar",
        "sourcesMergeAction": "Fusionar",
        "sourcesMergeDone": "Se fusionaron {count} servicio(s) en {target}.",
        "sourcesMergeFold": "Incluir (se desactivará)",
        "sourcesMergeIntro": "Combina varios servicios en uno. Las fuentes y dominios de los servicios elegidos pasan al objetivo; los vaciados se desactivan.",
        "sourcesMergeNeedsTwo": "Elige un objetivo y al menos un servicio para incluir.",
        "sourcesMergeTarget": "Conservar como objetivo",
        "sourcesMergeTitle": "Fusionar servicios",
    },
    "fr": {
        "newsFeedTitle": "Derniers articles",
        "aboutTitle": "À propos de PXke Algorand",
        "aboutLead": "PXke Algorand est une rédaction indépendante qui couvre la blockchain Algorand — marchés, protocole, gouvernance et projets qui s'y construisent.",
        "aboutHowHeading": "Comment nous publions",
        "aboutHowBody": "Notre couverture est assemblée automatiquement à partir d'événements on-chain, de données de marché planifiées et de sources communautaires surveillées, puis organisée en rubriques. Chaque article indique sa provenance.",
        "aboutAiHeading": "Rédigé avec l'IA",
        "aboutAiBody": "PXke Algorand publie du journalisme assisté par IA. Les articles sont rédigés par des modèles de langage à partir d'événements on-chain, de données de marché et de sources communautaires, sous revue éditoriale automatisée.",
        "aboutProvenanceHeading": "Provenance et transparence",
        "aboutProvenanceBody": "Les articles on-chain sont déclenchés par des transactions vérifiables et renvoient vers l'explorateur Algorand. Les articles marché s'appuient sur CoinGecko. Les contenus sponsorisés sont toujours signalés.",
        "aboutStandardsHeading": "Standards éditoriaux",
        "aboutStandardsBody": "Nous visons l'exactitude et une attribution claire. Cette publication n'est pas un conseil en investissement. Une erreur ? Contactez-nous via les liens source de n'importe quel article.",
        "articleByline": "Par {desk}",
        "articleLinkCopied": "Lien copié dans le presse-papiers",
        "articlePublicationDetailsHint": "Éditeur, date et lien source",
        "articleReadingTime": "{count} min de lecture",
        "articleRelatedTitle": "Articles connexes",
        "articleShare": "Partager",
        "bylineChainDesk": "Rédaction On-Chain",
        "bylineMarketsDesk": "Rédaction Marchés",
        "bylineNewsroom": "La rédaction",
        "footerAboutHeading": "À propos",
        "footerRights": "© {year} PXke Algorand. Couverture indépendante de l'écosystème Algorand.",
        "footerSectionsHeading": "Rubriques",
        "footerTagline": "Couverture indépendante et automatisée de l'écosystème Algorand.",
        "frontPageLatest": "Dernières",
        "frontPageMore": "Plus de la rédaction",
        "frontPageSectionStories": "Plus dans {section}",
        "frontPageTopStories": "À la une",
        "navAbout": "À propos",
        "navApps": "Apps",
        "navFrontPage": "Une",
        "navLatest": "Dernières",
        "navProductsMenuHint": "Explorer la plateforme",
        "navSections": "RUBRIQUES",
        "sectionCommunity": "Communauté",
        "sectionDevelopers": "Développeurs",
        "sectionEcosystem": "Écosystème",
        "sectionEmptyMessage": "Aucun article dans cette rubrique pour l'instant. Revenez bientôt.",
        "sectionEmptyTitle": "Rien ici pour l'instant",
        "sectionMarkets": "Marchés",
        "sectionSecurity": "Sécurité",
        "sourcesMerge": "Fusionner",
        "sourcesMergeAction": "Fusionner",
        "sourcesMergeDone": "{count} service(s) fusionné(s) dans {target}.",
        "sourcesMergeFold": "Inclure (sera désactivé)",
        "sourcesMergeIntro": "Regroupez plusieurs services en un. Les sources et domaines des services choisis passent à la cible ; les services vidés sont désactivés.",
        "sourcesMergeNeedsTwo": "Choisissez une cible et au moins un service à inclure.",
        "sourcesMergeTarget": "Conserver comme cible",
        "sourcesMergeTitle": "Fusionner des services",
    },
    "ar": {
        "aboutTitle": "حول PXke Algorand",
        "aboutLead": "PXke Algorand هي غرفة أخبار مستقلة تغطي بلوكتشين Algorand — أسواقها وبروتوكولها وحوكمتها والمشاريع المبنية عليها.",
        "aboutHowHeading": "كيف ننشر",
        "aboutHowBody": "نُجمّع التغطية تلقائياً من أحداث على السلسلة وبيانات السوق والمصادر المجتمعية المراقبة، ثم ننظمها في أقسام. كل قصة تتضمن مصدراً يمكنك تتبعه.",
        "aboutAiHeading": "مكتوب بالذكاء الاصطناعي",
        "aboutAiBody": "ينشر PXke Algorand صحافة بمساعدة الذكاء الاصطناعي. تُصاغ المقالات بنماذج لغوية من أحداث على السلسلة وبيانات السوق والمصادر المجتمعية، تحت مراجعة تحريرية آلية.",
        "aboutProvenanceHeading": "المصدر والشفافية",
        "aboutProvenanceBody": "تُفعَّل قصص على السلسلة بمعاملات قابلة للتحقق وترتبط بمستكشف Algorand. قصص السوق من CoinGecko. المحتوى المموَّل يُوسَّم دائماً.",
        "aboutStandardsHeading": "المعايير التحريرية",
        "aboutStandardsBody": "نسعى للدقة والإسناد الواضح. هذا ليس نصيحة استثمارية. لاحظت خطأً؟ تواصل عبر روابط المصدر في أي قصة.",
        "articleByline": "بقلم {desk}",
        "articleLinkCopied": "تم نسخ الرابط",
        "articlePublicationDetailsHint": "الناشر والتاريخ ورابط المصدر",
        "articleReadingTime": "{count} دقائق قراءة",
        "articleRelatedTitle": "قصص ذات صلة",
        "articleShare": "مشاركة",
        "bylineChainDesk": "مكتب على السلسلة",
        "bylineMarketsDesk": "مكتب الأسواق",
        "bylineNewsroom": "غرفة الأخبار",
        "footerAboutHeading": "حول",
        "footerRights": "© {year} PXke Algorand. تغطية مستقلة لنظام Algorand البيئي.",
        "footerSectionsHeading": "الأقسام",
        "footerTagline": "تغطية مستقلة وآلية لنظام Algorand البيئي.",
        "frontPageLatest": "الأحدث",
        "frontPageMore": "المزيد من غرفة الأخبار",
        "frontPageSectionStories": "المزيد في {section}",
        "frontPageTopStories": "أهم القصص",
        "navAbout": "حول",
        "navApps": "التطبيقات",
        "navFrontPage": "الصفحة الأولى",
        "navLatest": "الأحدث",
        "navProductsMenuHint": "استكشف المنصة",
        "navSections": "الأقسام",
        "sectionCommunity": "المجتمع",
        "sectionDevelopers": "المطورون",
        "sectionEcosystem": "النظام البيئي",
        "sectionEmptyMessage": "لا توجد قصص في هذا القسم بعد. عد قريباً.",
        "sectionEmptyTitle": "لا شيء هنا بعد",
        "sectionMarkets": "الأسواق",
        "sectionSecurity": "الأمن",
        "sourcesMerge": "دمج",
        "sourcesMergeAction": "دمج",
        "sourcesMergeDone": "تم دمج {count} خدمة في {target}.",
        "sourcesMergeFold": "طي (سيُعطَّل)",
        "sourcesMergeIntro": "ادمج عدة خدمات في واحدة. تنتقل مصادر ونطاقات الخدمات المختارة إلى الهدف؛ وتُعطَّل الفارغة.",
        "sourcesMergeNeedsTwo": "اختر هدفاً وخدمة واحدة على الأقل للطي.",
        "sourcesMergeTarget": "الإبقاء كهدف",
        "sourcesMergeTitle": "دمج الخدمات",
    },
}


def load_arb(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def message_keys(data: dict) -> list[str]:
    return [k for k in data if not k.startswith("@") and k != "@@locale"]


def preserve_placeholders(en_val: str, translated: str) -> str:
    """Keep {name} and ICU plural blocks from the English template."""
    placeholders = re.findall(r"\{[^}]+\}", en_val)
    if not placeholders:
        return translated
    # If translator dropped braces, re-inject from English in order.
    out = translated
    for ph in placeholders:
        if ph not in out:
            out = out.replace(ph.strip("{}"), ph.strip("{}"), 1)
    if "{" not in out and placeholders:
        # crude fallback: use english template structure
        return en_val
    return out


def translate_text(text: str, lang: str, translator) -> str:
    if not text.strip():
        return text
    # Skip mostly-ICU strings — copy structure from English after naive translate
    try:
        out = translator.translate(text)
        time.sleep(0.15)
        return preserve_placeholders(text, out)
    except Exception as exc:
        print(f"  translate error ({lang}): {exc!r} for {text[:40]!r}")
        return text


def build_locale(lang: str, en: dict, existing: dict | None, translator) -> dict:
    out: dict = {"@@locale": lang}
    patches = PATCHES.get(lang, {})
    for key in message_keys(en):
        meta_key = f"@{key}"
        if meta_key in en:
            out[meta_key] = deepcopy(en[meta_key])
        if key in patches:
            out[key] = patches[key]
        elif existing and key in existing:
            out[key] = existing[key]
        elif lang == "en":
            out[key] = en[key]
        else:
            out[key] = translate_text(en[key], lang, translator)
    return out


def main() -> None:
    try:
        from deep_translator import GoogleTranslator
    except ImportError as exc:
        raise SystemExit("pip install deep-translator") from exc

    en = load_arb(EN_PATH)
    for lang in TARGETS:
        path = L10N / f"app_{lang}.arb"
        existing = load_arb(path) if path.is_file() else None
        before = set(message_keys(existing)) if existing else set()
        print(f"=== {lang} ===")
        translator = GoogleTranslator(source="en", target=lang if lang != "zh" else "zh-CN")
        data = build_locale(lang, en, existing, translator)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        after = set(message_keys(data))
        added = sorted(after - before)
        print(f"  keys: {len(after)} (+{len(added)} new)")


if __name__ == "__main__":
    main()
