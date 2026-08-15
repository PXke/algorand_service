"""Hero/source image resolution (2026-07-09): three lanes published imageless — editorial briefs (service_id "editorial-brief:<id>" fails the domain-slug regex AND scrape_url "editorial://…" isn't fetchable), mail ("mail://message/N", same), and recompose (og_image was never stashed in review metadata). Plus the 2026-07-06 cross-domain hero guard dropped every CDN-hosted og:image (the majority pattern: cloudfront/cloudinary/ipfs/discourse-cdn)."""

from typing import Any, Never

import pytest

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


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    # _is_real_image retries once with a real sleep between attempts — no
    # test here needs that actual delay.
    monkeypatch.setattr("time.sleep", lambda *_a, **_kw: None)


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
    """Source-image extraction prefers the Sources block and skips social-platform links."""
    urls = source_urls_from_body(_BODY)
    assert urls == [
        "https://hesab.com/about",
        "https://coinedition.com/hesabpay-hafn/",
        "https://medium.com/wfp-innovation/post",
        "https://example.org/extra",
    ]


def test_source_urls_falls_back_to_inline_links_without_heading() -> None:
    """Source-image extraction falls back to inline body links when there is no Sources heading."""
    body = "See [docs](https://sealed.channel/docs) and [tw](https://twitter.com/sealed)."
    assert source_urls_from_body(body) == ["https://sealed.channel/docs"]


def test_editorial_and_mail_lanes_have_no_direct_candidates() -> None:
    # The failure that motivated the fallback: neither URL form is fetchable
    # and the service_id isn't a domain slug.
    """Editorial and mail source lanes have no directly fetchable candidate URLs."""
    assert (
        candidate_urls(
            source_url="editorial://brief/7616eb02", service_id="editorial-brief:7616eb02"
        )
        == []
    )
    assert candidate_urls(source_url="mail://message/7", service_id="") == []


def test_resolve_article_images_uses_body_sources_when_direct_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolution falls back to the body's Sources-block URLs when the direct source URL yields nothing."""
    fetched = []

    def fake_images(url: str) -> tuple[str, str]:
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


def test_dead_declared_og_falls_through_to_sources_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """Root-caused 2026-07-16 (Aramid + Subtopia published imageless): aramid.finance declares og/twitter images that both 404, and subtopia.io's og:image sits on the dead nftstorage.link IPFS gateway.

    The resolver accepted the DECLARED og unvalidated and stopped early, so
    the Sources-block fallback never ran — and the caller's post-hoc
    validation could only blank the result, not recover a better candidate.
    With ``validate`` wired into the resolver, a dead og is rejected
    mid-search and the cited links still get their chance.
    """
    pages = {
        "https://aramid.finance/": ("https://www.aramid.finance/og-image.jpg", ""),
        "https://hesab.com/about": ("https://hesab.com/images/logos/favicon.png", ""),
    }
    monkeypatch.setattr(si, "_images_from_url", lambda url: pages.get(url, ("", "")))

    def validate(image: str, _page_url: str, _kind: str) -> str:
        # The declared og 404s in the real incident — validator rejects it.
        if image == "https://www.aramid.finance/og-image.jpg":
            return ""
        return image

    og, _logo = resolve_article_images(
        source_url="https://aramid.finance/",
        service_id="aramid-finance",
        body=_BODY,
        validate=validate,
    )
    assert og == "https://hesab.com/images/logos/favicon.png"


def test_resolver_validation_is_anchored_to_the_declaring_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A GitHub/docs link cited in Sources advertises an image on ITS own CDN (opengraph.githubassets.com) — foreign to the article's subject site but correct for the declaring page. Validation must therefore be anchored to the page each image was found on, not the article's source_url."""
    pages = {
        "https://hesab.com/about": ("https://cdn.hesab-images.net/og.png", ""),
    }
    monkeypatch.setattr(si, "_images_from_url", lambda url: pages.get(url, ("", "")))
    seen: list[tuple[str, str]] = []

    def validate(image: str, page_url: str, _kind: str) -> str:
        seen.append((image, page_url))
        return image

    og, _ = resolve_article_images(
        source_url="editorial://brief/x",
        service_id="editorial-brief:x",
        body=_BODY,
        validate=validate,
    )
    assert og == "https://cdn.hesab-images.net/og.png"
    assert ("https://cdn.hesab-images.net/og.png", "https://hesab.com/about") in seen


def test_resolver_without_validate_keeps_first_declared_og(monkeypatch: pytest.MonkeyPatch) -> None:
    # Legacy behavior (backfill --dry-run style callers): no validator, first
    # declared og wins unvalidated.
    """Without a validator, the first declared og:image is kept unvalidated (legacy backfill dry-run behavior)."""
    monkeypatch.setattr(si, "_images_from_url", lambda _url: ("https://site.com/og.png", ""))
    og, _ = resolve_article_images(source_url="https://site.com/", service_id="", body="")
    assert og == "https://site.com/og.png"


def test_resolve_article_images_prefers_direct_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prefers a direct resolve_source_images hit over ever falling back to body sources."""
    monkeypatch.setattr(si, "resolve_source_images", lambda **_kw: ("https://site.com/og.png", ""))
    monkeypatch.setattr(
        si, "_images_from_url", lambda _url: (_ for _ in ()).throw(AssertionError("no fallback"))
    )
    og, _ = resolve_article_images(source_url="https://site.com/", service_id="", body=_BODY)
    assert og == "https://site.com/og.png"


def test_hero_allows_cdn_hosted_og_images() -> None:
    # The real-world majority: share image on a CDN domain, not the site's own.
    """A hero image hosted on a known CDN domain (not the site's own) is still accepted."""
    for image in (
        "https://ipfs.vestigelabs.org/ipfs/QmSDxw",  # rug.ninja
        "https://dnldumbki4b4x.cloudfront.net/website/img/social/preview.png",  # lofty
        "https://res.cloudinary.com/noahapp/image/upload/x.png",  # noah
        "https://us1.discourse-cdn.com/flex016/uploads/algorand/x.png",  # forum
    ):
        assert _plausible_image_host(image, "https://rug.ninja/"), image
        assert _with_hero_image("body", image, "t", source_url="https://rug.ninja/") != "body"


def test_hero_url_encodes_literal_spaces() -> None:
    """A raw og:image URL with unencoded spaces (found live 2026-08-09, risein.com) must not
    break markdown link parsing -- CommonMark truncates a bare link destination at the first
    unescaped whitespace, spilling the rest as literal text right after the image."""
    image = "https://files.risein.com/courses/algorand/SCxj-Build on Algorand Course.png"
    out = _with_hero_image("body", image, "t", source_url="https://risein.com/courses/x")
    assert "SCxj-Build%20on%20Algorand%20Course.png" in out
    assert " on Algorand Course.png)" not in out


def test_hero_allows_same_platform_shared_media_host() -> None:
    # Live 2026-07-13 bug: every Medium-sourced article silently lost its hero
    # image because domain_from_url deliberately keeps multi-tenant platform
    # subdomains distinct (so unrelated authors aren't merged as one source),
    # which made Medium's own shared image CDN (miro.medium.com) look foreign
    # to any one *.medium.com publication.
    """A shared-media host on the same publishing platform (e.g. Medium's own CDN) is accepted; a genuinely different platform is not."""
    assert _plausible_image_host(
        "https://miro.medium.com/v2/resize:fit:2400/1*abc.jpeg",
        "https://valar-staking.medium.com",
    )
    # A genuinely different platform must still be rejected.
    assert not _plausible_image_host(
        "https://miro.medium.com/v2/resize:fit:2400/1*abc.jpeg",
        "https://some-blog.substack.com",
    )


def test_hero_allows_gitbook_ogimage_host() -> None:
    # 2026-07-14 backfill finding: a docs site's own GitBook-hosted OG-image
    # generator (defly.gitbook.io serving docs.defly.app) is the same
    # shared-host pattern as Medium/Cloudinary/etc — a perfectly good
    # 1200x630 image was wrongly rejected before "gitbook" was recognized.
    """A docs site's own GitBook-hosted OG-image generator host is accepted as plausible."""
    assert _plausible_image_host(
        "https://defly.gitbook.io/defly-manual/~gitbook/ogimage/abc123",
        "https://docs.defly.app",
    )


def test_hero_allows_any_image_for_non_http_source() -> None:
    # editorial://brief/<uuid> and mail://message/<n> are synthetic, non-
    # fetchable source identifiers, not real websites — domain_from_url
    # parses their netloc as if it were a real hostname ("brief", "message"),
    # which wrongly rejected perfectly good images (algorand.co, GitBook OG
    # images) the first time a re-validation backfill exercised this
    # combination (2026-07-14). Only http(s) sources have a real domain to
    # compare against at all.
    """Any image host is accepted for a synthetic, non-HTTP source (editorial://, mail://) since there's no real domain to compare against."""
    assert _plausible_image_host(
        "https://algorand.co/hubfs/DeFi%20protocols-2.png",
        "editorial://brief/1f1719f9-6dd0-4ca3-9961-812d9851ebc6",
    )
    assert _plausible_image_host(
        "https://x402.org/wp-content/uploads/sites/10/2026/06/Untitled-design.png",
        "mail://message/7",
    )


def test_hero_still_drops_foreign_website_images() -> None:
    # The case the guard exists for: a news aggregator hotlinking another
    # news site's stock photo.
    """A foreign website's hotlinked stock photo is rejected and never becomes the hero image."""
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
    """An image on the article's own domain always passes the plausibility check."""
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
    """An implausible foreign image_url is dropped by validated hero resolution too, not just the body embed."""
    assert (
        _validated_hero(
            "https://www.readvertising.org/og-image.png", "https://www.scottgerrard.com"
        )
        == ""
    )


def test_validated_hero_keeps_plausible_image_url() -> None:
    """A plausible image_url on the source's own domain is kept unchanged."""
    assert (
        _validated_hero("https://vestige.fi/og-image.png", "https://vestige.fi")
        == "https://vestige.fi/og-image.png"
    )
    assert (
        _validated_hero("https://res.cloudinary.com/noahapp/image/upload/x.png", "https://noah.com")
        == "https://res.cloudinary.com/noahapp/image/upload/x.png"
    )


def test_validated_hero_does_not_shape_reject_icon_urls() -> None:
    # 2026-07-14 correction: whether an icon-shaped URL is too small/blurry
    # to use is a pixel-level judgment (_is_real_image), not a URL-shape one
    # — a 192x192 apple-touch-icon (AlgoVanity) is perfectly usable even
    # though its URL "looks like" a logo. _validated_hero (pure, no network)
    # only does domain-plausibility; see _validated_hero_checked for the
    # actual quality gate.
    """Icon-shaped URLs are not shape-rejected by the pure domain-plausibility gate; quality is a separate pixel-level check."""
    assert (
        _validated_hero("https://a-wallet.net/favicon.ico", "https://a-wallet.net")
        == "https://a-wallet.net/favicon.ico"
    )


def _fake_response(*, content: bytes, status_ok: bool = True) -> Any:  # noqa: ANN401 -- test double / fake response
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


def test_validated_hero_checked_drops_decoy_pixel(monkeypatch: pytest.MonkeyPatch) -> None:
    # geographia.com.br/docs.vestigelabs.org-shaped case: a real, non-logo-
    # shaped URL that resolves to a 1x1 fully-transparent decoy pixel instead
    # of an error (2026-07-14 root cause).
    """A 1x1 fully-transparent decoy pixel is dropped by the pixel-quality check."""
    monkeypatch.setattr(
        "app.core.net_guard.guarded_get",
        lambda *_a, **_kw: _fake_response(content=_png_bytes(size=(1, 1), transparent=True)),
    )
    assert (
        _validated_hero_checked("https://geographia.com.br/hero.png", "https://geographia.com.br")
        == ""
    )


def test_validated_hero_checked_drops_tiny_image(monkeypatch: pytest.MonkeyPatch) -> None:
    """A tiny (8x8) image is dropped by the pixel-quality check."""
    monkeypatch.setattr(
        "app.core.net_guard.guarded_get",
        lambda *_a, **_kw: _fake_response(content=_png_bytes(size=(8, 8))),
    )
    assert _validated_hero_checked("https://vestige.fi/hero.png", "https://vestige.fi") == ""


def test_validated_hero_checked_drops_real_favicon_size(monkeypatch: pytest.MonkeyPatch) -> None:
    # a-wallet.net/downbad.farm-shaped case: a genuine favicon (48x48, the
    # measured real-world size) is too small/blurry to use as a hero.
    """A real favicon-sized (48x48) image is dropped as too small/blurry for a hero."""
    monkeypatch.setattr(
        "app.core.net_guard.guarded_get",
        lambda *_a, **_kw: _fake_response(content=_png_bytes(size=(48, 48))),
    )
    assert _validated_hero_checked("https://a-wallet.net/favicon.ico", "https://a-wallet.net") == ""


def test_validated_hero_checked_keeps_good_apple_touch_icon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # AlgoVanity's real apple-touch-icon.png measures 192x192 — a decent icon,
    # not a blurry favicon, even though the URL "looks like" a logo.
    """A genuinely good apple-touch-icon (192x192) passes the pixel-quality check despite its icon-like URL."""
    monkeypatch.setattr(
        "app.core.net_guard.guarded_get",
        lambda *_a, **_kw: _fake_response(content=_png_bytes(size=(192, 192))),
    )
    assert (
        _validated_hero_checked(
            "https://algovanity.com/apple-touch-icon.png", "https://algovanity.com"
        )
        == "https://algovanity.com/apple-touch-icon.png"
    )


def test_validated_hero_checked_logo_kind_drops_borderline_icon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bare brand icon (kind="logo", no declared og:image anywhere) needs a stricter floor than a real share image -- 192x192 clears the "og" bar (120) but not the "logo" one (256), since a favicon-shaped image that small still looks blurry blown up to full hero width."""
    monkeypatch.setattr(
        "app.core.net_guard.guarded_get",
        lambda *_a, **_kw: _fake_response(content=_png_bytes(size=(192, 192))),
    )
    assert (
        _validated_hero_checked(
            "https://museum.datahistory.org/assets/favicon.ico",
            "https://museum.datahistory.org",
            "logo",
        )
        == ""
    )


def test_validated_hero_checked_logo_kind_keeps_genuinely_large_icon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A genuinely large declared icon (>=256px) passes the stricter logo-kind floor."""
    monkeypatch.setattr(
        "app.core.net_guard.guarded_get",
        lambda *_a, **_kw: _fake_response(content=_png_bytes(size=(512, 512))),
    )
    assert (
        _validated_hero_checked(
            "https://museum.datahistory.org/assets/icon-512.png",
            "https://museum.datahistory.org",
            "logo",
        )
        == "https://museum.datahistory.org/assets/icon-512.png"
    )


def test_validated_hero_checked_keeps_real_image(monkeypatch: pytest.MonkeyPatch) -> None:
    """A properly sized real image (1200x630) passes the pixel-quality check."""
    monkeypatch.setattr(
        "app.core.net_guard.guarded_get",
        lambda *_a, **_kw: _fake_response(content=_png_bytes(size=(1200, 630))),
    )
    assert (
        _validated_hero_checked("https://vestige.fi/hero.png", "https://vestige.fi")
        == "https://vestige.fi/hero.png"
    )


def test_validated_hero_checked_retries_once_on_transient_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 2026-07-14: a batch backfill hitting dozens of external hosts back-to-
    # back saw several perfectly good images (algorand.co, GitBook OG images,
    # x402.org, hesab.com) wrongly cleared by a single transient fetch
    # failure. One retry before giving up fixes that.
    """A transient fetch failure is retried once before the image is accepted."""
    calls = {"n": 0}

    def _flaky(*_a: object, **_kw: object) -> Any:  # noqa: ANN401 -- test double / fake response
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("transient")
        return _fake_response(content=_png_bytes(size=(1200, 630)))

    monkeypatch.setattr("app.core.net_guard.guarded_get", _flaky)
    assert (
        _validated_hero_checked("https://vestige.fi/hero.png", "https://vestige.fi")
        == "https://vestige.fi/hero.png"
    )
    assert calls["n"] == 2


def test_validated_hero_checked_drops_on_fetch_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A persistent fetch failure drops the candidate after the retry is exhausted."""

    def _boom(*_a: object, **_kw: object) -> Never:
        raise RuntimeError("network down")

    monkeypatch.setattr("app.core.net_guard.guarded_get", _boom)
    assert _validated_hero_checked("https://vestige.fi/hero.png", "https://vestige.fi") == ""


def test_validated_hero_checked_never_fetches_for_implausible_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Domain-plausibility rejection happens in the pure gate — no network
    # call should even be attempted for an obviously foreign image host.
    """An implausible domain is rejected by the pure gate without ever attempting a network fetch."""
    monkeypatch.setattr(
        publish_tasks,
        "_is_real_image",
        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("should not fetch")),
    )
    assert (
        _validated_hero_checked(
            "https://cnews24.ru/uploads/2023/photo.jpg", "https://cryptonews.net/news/x/"
        )
        == ""
    )
