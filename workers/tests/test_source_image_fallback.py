"""Hero/source image resolution (2026-07-09): three lanes published imageless —
editorial briefs (service_id "editorial-brief:<id>" fails the domain-slug
regex AND scrape_url "editorial://…" isn't fetchable), mail ("mail://message/N",
same), and recompose (og_image was never stashed in review metadata). Plus the
2026-07-06 cross-domain hero guard dropped every CDN-hosted og:image (the
majority pattern: cloudfront/cloudinary/ipfs/discourse-cdn)."""

import app.modules.newspaper.source_image as si
from app.modules.newspaper.source_image import (
    candidate_urls,
    resolve_article_images,
    source_urls_from_body,
)
from app.modules.newspaper.tasks.publish_tasks import _plausible_image_host, _with_hero_image

_BODY = """HesabPay runs on Algorand.

Inline citation: [case study](https://algorand.co/case-studies/hesabpay).

## Sources

- [HesabPay](https://hesab.com/about)
- [X profile](https://x.com/hesabpay)
- [Telegram](https://t.me/hesabpay)
- [Coinedition](https://coinedition.com/hesabpay-hafn/)
- [WFP](https://medium.com/wfp-innovation/post)
- [Extra beyond cap](https://example.org/extra)
"""


def test_source_urls_prefers_sources_block_and_skips_socials() -> None:
    urls = source_urls_from_body(_BODY)
    assert urls == [
        "https://hesab.com/about",
        "https://coinedition.com/hesabpay-hafn/",
        "https://medium.com/wfp-innovation/post",
        "https://example.org/extra",
    ]


def test_source_urls_falls_back_to_inline_links_without_heading() -> None:
    body = "See [docs](https://sealed.channel/docs) and [tw](https://twitter.com/sealed)."
    assert source_urls_from_body(body) == ["https://sealed.channel/docs"]


def test_editorial_and_mail_lanes_have_no_direct_candidates() -> None:
    # The failure that motivated the fallback: neither URL form is fetchable
    # and the service_id isn't a domain slug.
    assert candidate_urls(
        source_url="editorial://brief/7616eb02", service_id="editorial-brief:7616eb02"
    ) == []
    assert candidate_urls(source_url="mail://message/7", service_id="") == []


def test_resolve_article_images_uses_body_sources_when_direct_fails(monkeypatch) -> None:
    fetched = []

    def fake_images(url):
        fetched.append(url)
        if url == "https://hesab.com/about":
            return "https://hesab.com/images/logos/favicon.png", ""
        return "", ""

    monkeypatch.setattr(si, "_images_from_url", fake_images)
    og, _logo = resolve_article_images(
        source_url="editorial://brief/x", service_id="editorial-brief:x", body=_BODY
    )
    assert og == "https://hesab.com/images/logos/favicon.png"
    # Direct candidates were empty, so the first fetch is the first body source.
    assert fetched[0] == "https://hesab.com/about"


def test_resolve_article_images_prefers_direct_source(monkeypatch) -> None:
    monkeypatch.setattr(
        si, "resolve_source_images", lambda **kw: ("https://site.com/og.png", "")
    )
    monkeypatch.setattr(
        si, "_images_from_url", lambda url: (_ for _ in ()).throw(AssertionError("no fallback"))
    )
    og, _ = resolve_article_images(source_url="https://site.com/", service_id="", body=_BODY)
    assert og == "https://site.com/og.png"


def test_hero_allows_cdn_hosted_og_images() -> None:
    # The real-world majority: share image on a CDN domain, not the site's own.
    for image in (
        "https://ipfs.vestigelabs.org/ipfs/QmSDxw",  # rug.ninja
        "https://dnldumbki4b4x.cloudfront.net/website/img/social/preview.png",  # lofty
        "https://res.cloudinary.com/noahapp/image/upload/x.png",  # noah
        "https://us1.discourse-cdn.com/flex016/uploads/algorand/x.png",  # forum
    ):
        assert _plausible_image_host(image, "https://rug.ninja/"), image
        assert _with_hero_image("body", image, "t", source_url="https://rug.ninja/") != "body"


def test_hero_still_drops_foreign_website_images() -> None:
    # The case the guard exists for: a news aggregator hotlinking another
    # news site's stock photo.
    assert not _plausible_image_host(
        "https://cnews24.ru/uploads/2023/photo.jpg", "https://cryptonews.net/news/x/"
    )
    body = _with_hero_image(
        "body",
        "https://cnews24.ru/uploads/2023/photo.jpg",
        "t",
        source_url="https://cryptonews.net/news/x/",
    )
    assert body == "body"


def test_hero_same_domain_still_passes() -> None:
    out = _with_hero_image(
        "body", "https://vestige.fi/og-image.png", "t", source_url="https://vestige.fi"
    )
    assert out.startswith("![t](https://vestige.fi/og-image.png)")
