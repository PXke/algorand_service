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
from app.modules.newspaper.tasks import publish_tasks
from app.modules.newspaper.tasks.publish_tasks import (
    _plausible_image_host,
    _validated_hero,
    _validated_hero_checked,
    _with_hero_image,
)

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


def test_hero_allows_same_platform_shared_media_host() -> None:
    # Live 2026-07-13 bug: every Medium-sourced article silently lost its hero
    # image because domain_from_url deliberately keeps multi-tenant platform
    # subdomains distinct (so unrelated authors aren't merged as one source),
    # which made Medium's own shared image CDN (miro.medium.com) look foreign
    # to any one *.medium.com publication.
    assert _plausible_image_host(
        "https://miro.medium.com/v2/resize:fit:2400/1*abc.jpeg",
        "https://valar-staking.medium.com",
    )
    # A genuinely different platform must still be rejected.
    assert not _plausible_image_host(
        "https://miro.medium.com/v2/resize:fit:2400/1*abc.jpeg",
        "https://some-blog.substack.com",
    )


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


def test_validated_hero_drops_implausible_image_url_too() -> None:
    # The 2026-07-10 bug: _plausible_image_host already kept a foreign
    # og:image out of the BODY, but image_url (feed tile + OG card) used the
    # raw value unchecked — a template site's stale, unrelated og:image (here,
    # scottgerrard.com literally serving readvertising.org's share image)
    # still became the article's thumbnail/social card every time.
    assert (
        _validated_hero(
            "https://www.readvertising.org/og-image.png", "https://www.scottgerrard.com"
        )
        == ""
    )


def test_validated_hero_keeps_plausible_image_url() -> None:
    assert (
        _validated_hero("https://vestige.fi/og-image.png", "https://vestige.fi")
        == "https://vestige.fi/og-image.png"
    )
    assert (
        _validated_hero(
            "https://res.cloudinary.com/noahapp/image/upload/x.png", "https://noah.com"
        )
        == "https://res.cloudinary.com/noahapp/image/upload/x.png"
    )


def test_validated_hero_does_not_shape_reject_icon_urls() -> None:
    # 2026-07-14 correction: whether an icon-shaped URL is too small/blurry
    # to use is a pixel-level judgment (_is_real_image), not a URL-shape one
    # — a 192x192 apple-touch-icon (AlgoVanity) is perfectly usable even
    # though its URL "looks like" a logo. _validated_hero (pure, no network)
    # only does domain-plausibility; see _validated_hero_checked for the
    # actual quality gate.
    assert (
        _validated_hero("https://a-wallet.net/favicon.ico", "https://a-wallet.net")
        == "https://a-wallet.net/favicon.ico"
    )


def _fake_response(*, content: bytes, status_ok: bool = True):
    class _Resp:
        def __init__(self) -> None:
            self.content = content

        def raise_for_status(self) -> None:
            if not status_ok:
                raise RuntimeError("bad status")

    return _Resp()


def _png_bytes(*, size: tuple[int, int], mode: str = "RGB", transparent: bool = False) -> bytes:
    from io import BytesIO

    from PIL import Image

    if transparent:
        img = Image.new("RGBA", size, (0, 0, 0, 0))
    else:
        img = Image.new(mode, size, (10, 20, 30))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_validated_hero_checked_drops_decoy_pixel(monkeypatch) -> None:
    # geographia.com.br/docs.vestigelabs.org-shaped case: a real, non-logo-
    # shaped URL that resolves to a 1x1 fully-transparent decoy pixel instead
    # of an error (2026-07-14 root cause).
    monkeypatch.setattr(
        "app.core.net_guard.guarded_get",
        lambda *a, **kw: _fake_response(content=_png_bytes(size=(1, 1), transparent=True)),
    )
    assert (
        _validated_hero_checked("https://geographia.com.br/hero.png", "https://geographia.com.br")
        == ""
    )


def test_validated_hero_checked_drops_tiny_image(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.core.net_guard.guarded_get",
        lambda *a, **kw: _fake_response(content=_png_bytes(size=(8, 8))),
    )
    assert _validated_hero_checked("https://vestige.fi/hero.png", "https://vestige.fi") == ""


def test_validated_hero_checked_drops_real_favicon_size(monkeypatch) -> None:
    # a-wallet.net/downbad.farm-shaped case: a genuine favicon (48x48, the
    # measured real-world size) is too small/blurry to use as a hero.
    monkeypatch.setattr(
        "app.core.net_guard.guarded_get",
        lambda *a, **kw: _fake_response(content=_png_bytes(size=(48, 48))),
    )
    assert (
        _validated_hero_checked("https://a-wallet.net/favicon.ico", "https://a-wallet.net") == ""
    )


def test_validated_hero_checked_keeps_good_apple_touch_icon(monkeypatch) -> None:
    # AlgoVanity's real apple-touch-icon.png measures 192x192 — a decent icon,
    # not a blurry favicon, even though the URL "looks like" a logo.
    monkeypatch.setattr(
        "app.core.net_guard.guarded_get",
        lambda *a, **kw: _fake_response(content=_png_bytes(size=(192, 192))),
    )
    assert (
        _validated_hero_checked("https://algovanity.com/apple-touch-icon.png", "https://algovanity.com")
        == "https://algovanity.com/apple-touch-icon.png"
    )


def test_validated_hero_checked_keeps_real_image(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.core.net_guard.guarded_get",
        lambda *a, **kw: _fake_response(content=_png_bytes(size=(1200, 630))),
    )
    assert (
        _validated_hero_checked("https://vestige.fi/hero.png", "https://vestige.fi")
        == "https://vestige.fi/hero.png"
    )


def test_validated_hero_checked_drops_on_fetch_failure(monkeypatch) -> None:
    def _boom(*a, **kw):
        raise RuntimeError("network down")

    monkeypatch.setattr("app.core.net_guard.guarded_get", _boom)
    assert _validated_hero_checked("https://vestige.fi/hero.png", "https://vestige.fi") == ""


def test_validated_hero_checked_never_fetches_for_implausible_domain(monkeypatch) -> None:
    # Domain-plausibility rejection happens in the pure gate — no network
    # call should even be attempted for an obviously foreign image host.
    monkeypatch.setattr(
        publish_tasks,
        "_is_real_image",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("should not fetch")),
    )
    assert (
        _validated_hero_checked(
            "https://cnews24.ru/uploads/2023/photo.jpg", "https://cryptonews.net/news/x/"
        )
        == ""
    )
