"""SPA (browser://) sources must classify as web so the per-domain daily cap and
diversity cooldown apply — otherwise they silently bypass both."""

from app.modules.crawler.domain_tracker import domain_from_url
from app.modules.newspaper.tasks.publish_tasks import _source_kind_from_url


def test_browser_url_classifies_as_web():
    assert _source_kind_from_url("browser://https://bsky.app/profile/x") == "web"
    assert _source_kind_from_url("https://nf.domains") == "web"
    assert _source_kind_from_url("youtube://UC_x5XG1OV2P6uZZ5FSM9Ttw") == "youtube"
    assert _source_kind_from_url("mail://inbox") == "mail"


def test_domain_from_browser_url_resolves_real_host():
    # Regression: used to return "https" for browser://https://… , giving every
    # SPA source one shared bogus cooldown bucket.
    assert domain_from_url("browser://https://bsky.app/profile/x") == "bsky.app"
    assert domain_from_url("browser://bsky.app") == "bsky.app"
    assert domain_from_url("https://bsky.app/profile/x") == "bsky.app"
