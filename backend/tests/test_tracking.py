from app.core.tracking import tracking_opted_out_from_cookie, tracking_opted_out_from_headers


def test_tracking_opted_out_from_cookie() -> None:
    assert tracking_opted_out_from_cookie("pxke_no_track=1; path=/")
    assert not tracking_opted_out_from_cookie("")
    assert not tracking_opted_out_from_cookie("pxke_no_track=0")


def test_tracking_opted_out_from_headers() -> None:
    # 2026-07-12: the pageview beacon route (seo/api/routes.py:beacon_pageview)
    # called an undefined `_tracking_opted_out(request)` — a live NameError on
    # every beacon POST in prod. The real function takes headers, not the
    # request object; pin that shape here since it's what the route now calls.
    assert tracking_opted_out_from_headers({"cookie": "pxke_no_track=1"})
    assert tracking_opted_out_from_headers({"Cookie": "pxke_no_track=1"})
    assert not tracking_opted_out_from_headers({})
    assert not tracking_opted_out_from_headers({"cookie": "other=1"})
