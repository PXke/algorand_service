"""Day-ahead `to_compose` selection: human-slot-stays-empty-if-unpicked, N-1 platform fill, and per-service dedup excluding the human's own pick.

Uses the shared `fake_artifact_session` fixture (conftest.py) -- via
`@pytest.mark.usefixtures` for tests that only need its monkeypatching side
effect, or as a plain parameter for tests that inspect its in-memory tables
directly.

This file only exercises select_to_compose_for_day/preview_to_compose_for_day
directly as plain functions -- the live compose trigger that CALLS
select_to_compose_for_day on a beat (queue_drain_tasks.select_to_compose_for_today_task)
and composes from its output (queue_drain_tasks.drain_to_compose) is covered
separately in the queue_drain_tasks test files.
"""

from __future__ import annotations

import datetime as dt

import pytest
from conftest import FakeArtifactSession


@pytest.mark.usefixtures("fake_artifact_session")
def test_human_slot_stays_empty_when_no_pin_by_cutoff() -> None:
    """Explicit owner decision: if nobody pinned an artifact for the day, slot 0 stays UNFILLED -- the platform must never backfill it to avoid overcomposing."""
    from algorand_shared.artifact_store import insert_artifact
    from algorand_shared.to_compose_selection import (
        list_to_compose_for_day,
        select_to_compose_for_day,
    )

    insert_artifact(service_id="svc-a", url=None, channel="brief", content="a")
    insert_artifact(service_id="svc-b", url=None, channel="brief", content="b")

    result = select_to_compose_for_day("2026-08-26")

    assert result["human_picked"] is False
    lanes = [row["lane"] for row in list_to_compose_for_day("2026-08-26")]
    assert "human" not in lanes


@pytest.mark.usefixtures("fake_artifact_session")
def test_human_pin_fills_slot_zero() -> None:
    """A pinned artifact takes slot 0 with lane='human'."""
    from algorand_shared.artifact_store import insert_artifact, pin_artifact_for_day
    from algorand_shared.to_compose_selection import (
        list_to_compose_for_day,
        select_to_compose_for_day,
    )

    picked_id, _ = insert_artifact(
        service_id="svc-picked", url=None, channel="brief", content="picked"
    )
    pin_artifact_for_day(picked_id, "2026-08-26")

    result = select_to_compose_for_day("2026-08-26")

    assert result["human_picked"] is True
    rows = list_to_compose_for_day("2026-08-26")
    assert rows[0]["slot"] == 0
    assert rows[0]["lane"] == "human"
    assert rows[0]["artifact_id"] == picked_id


@pytest.mark.usefixtures("fake_artifact_session")
def test_list_to_compose_for_day_isoformats_picked_at() -> None:
    """picked_at comes back as an ISO string, not a raw datetime -- required so this is safe to hand straight to a JSON-serializing transport (Celery's default JSON serializer wraps a raw datetime in a `{"__type__": ...}` envelope, which would otherwise leak into the admin API response)."""
    from algorand_shared.artifact_store import insert_artifact, pin_artifact_for_day
    from algorand_shared.to_compose_selection import (
        list_to_compose_for_day,
        select_to_compose_for_day,
    )

    picked_id, _ = insert_artifact(
        service_id="svc-picked", url=None, channel="brief", content="picked"
    )
    pin_artifact_for_day(picked_id, "2026-08-26")
    select_to_compose_for_day("2026-08-26", now=dt.datetime(2026, 8, 26, 0, 5, tzinfo=dt.UTC))

    rows = list_to_compose_for_day("2026-08-26")

    assert rows[0]["picked_at"] == "2026-08-26T00:05:00+00:00"


@pytest.mark.usefixtures("fake_artifact_session")
def test_a_pin_for_a_different_day_is_not_used() -> None:
    """A pin set for some OTHER day must not accidentally fill today's/tomorrow's human slot."""
    from algorand_shared.artifact_store import insert_artifact, pin_artifact_for_day
    from algorand_shared.to_compose_selection import select_to_compose_for_day

    picked_id, _ = insert_artifact(
        service_id="svc-picked", url=None, channel="brief", content="picked"
    )
    pin_artifact_for_day(picked_id, "2026-09-01")

    result = select_to_compose_for_day("2026-08-26")
    assert result["human_picked"] is False


@pytest.mark.usefixtures("fake_artifact_session")
def test_platform_fills_n_minus_1_slots_by_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no human pick, N-1 platform slots go to the top-priority pending artifacts."""
    from algorand_shared.artifact_store import insert_artifact, update_artifact_priority
    from algorand_shared.to_compose_selection import (
        list_to_compose_for_day,
        select_to_compose_for_day,
    )
    from app.core import config as cfg

    monkeypatch.setattr(cfg, "NEWS_MAX_ARTICLES_PER_DAY", 3)  # N=3 -> 2 platform slots

    ids = []
    for i, prio in enumerate([1.0, 9.0, 5.0, 7.0]):
        aid, _ = insert_artifact(
            service_id=f"svc-{i}", url=None, channel="brief", content=f"content {i}"
        )
        update_artifact_priority(aid, prio)
        ids.append(aid)
    # priorities: svc-0=1.0, svc-1=9.0, svc-2=5.0, svc-3=7.0
    # top-2 by priority: svc-1 (9.0), svc-3 (7.0)

    result = select_to_compose_for_day("2026-08-26")

    assert result["human_picked"] is False
    assert result["platform_slots_available"] == 2
    assert result["platform_slots_filled"] == 2
    rows = list_to_compose_for_day("2026-08-26")
    assert [r["lane"] for r in rows] == ["platform", "platform"]
    assert [r["artifact_id"] for r in rows] == [ids[1], ids[3]]


@pytest.mark.usefixtures("fake_artifact_session")
def test_platform_fill_respects_one_pending_per_service_dedup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two DIFFERENT artifacts can never coexist pending for the same service_id (insert_artifact's own dedup already guarantees this), so the platform fill naturally never double-picks one service -- this pins that guarantee end to end through selection."""
    from algorand_shared.artifact_store import insert_artifact, update_artifact_priority
    from algorand_shared.to_compose_selection import (
        list_to_compose_for_day,
        select_to_compose_for_day,
    )
    from app.core import config as cfg

    monkeypatch.setattr(cfg, "NEWS_MAX_ARTICLES_PER_DAY", 4)  # N=4 -> 3 platform slots

    # Same service_id twice: insert_artifact's dedup means only the SECOND
    # survives as pending for "svc-same".
    insert_artifact(service_id="svc-same", url=None, channel="crawler", content="old diff")
    newest_id, _ = insert_artifact(
        service_id="svc-same", url=None, channel="crawler", content="new diff"
    )
    update_artifact_priority(newest_id, 8.0)
    other_id, _ = insert_artifact(service_id="svc-other", url=None, channel="crawler", content="other")
    update_artifact_priority(other_id, 6.0)

    result = select_to_compose_for_day("2026-08-26")

    assert result["platform_slots_filled"] == 2  # only 2 pending artifacts exist at all
    rows = list_to_compose_for_day("2026-08-26")
    service_ids = {r["service_id"] for r in rows}
    assert service_ids == {"svc-same", "svc-other"}


@pytest.mark.usefixtures("fake_artifact_session")
def test_platform_fill_excludes_the_human_picks_own_service(monkeypatch: pytest.MonkeyPatch) -> None:
    """'N-1 platform slots ... excluding whatever the human already picked' -- a second pending artifact for the SAME service_id as the human pick must not also take a platform slot."""
    from algorand_shared.artifact_store import (
        insert_artifact,
        pin_artifact_for_day,
        update_artifact_priority,
    )
    from algorand_shared.to_compose_selection import (
        list_to_compose_for_day,
        select_to_compose_for_day,
    )
    from app.core import config as cfg

    monkeypatch.setattr(cfg, "NEWS_MAX_ARTICLES_PER_DAY", 3)

    human_id, _ = insert_artifact(service_id="svc-human", url=None, channel="brief", content="pinned")
    pin_artifact_for_day(human_id, "2026-08-26")
    update_artifact_priority(human_id, 10.0)

    platform_id, _ = insert_artifact(
        service_id="svc-platform", url=None, channel="crawler", content="p"
    )
    update_artifact_priority(platform_id, 5.0)

    result = select_to_compose_for_day("2026-08-26")

    assert result["human_picked"] is True
    assert result["platform_slots_filled"] == 1
    rows = list_to_compose_for_day("2026-08-26")
    assert [r["artifact_id"] for r in rows] == [human_id, platform_id]


def test_selected_artifacts_leave_the_pending_lane(
    fake_artifact_session: FakeArtifactSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both the human pick and every platform pick transition pending -> selected and drop out of the pending index."""
    from algorand_shared.artifact_store import SELECTED, insert_artifact, pin_artifact_for_day
    from algorand_shared.to_compose_selection import select_to_compose_for_day
    from app.core import config as cfg

    monkeypatch.setattr(cfg, "NEWS_MAX_ARTICLES_PER_DAY", 2)

    human_id, _ = insert_artifact(service_id="svc-human", url=None, channel="brief", content="pinned")
    pin_artifact_for_day(human_id, "2026-08-26")
    platform_id, _ = insert_artifact(service_id="svc-p", url=None, channel="crawler", content="p")

    select_to_compose_for_day("2026-08-26")

    assert fake_artifact_session.artifacts[human_id]["status"] == SELECTED
    assert fake_artifact_session.artifacts[platform_id]["status"] == SELECTED
    assert len(fake_artifact_session.pending) == 0


@pytest.mark.usefixtures("fake_artifact_session")
def test_platform_slots_available_floors_at_zero_when_cap_is_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """N=1 (the minimum allowed cap) leaves zero platform slots -- must not go negative or (an earlier bug caught by this test) admit one extra artifact via an off-by-one in the fill loop's break check."""
    from algorand_shared.artifact_store import insert_artifact
    from algorand_shared.to_compose_selection import select_to_compose_for_day
    from app.core import config as cfg

    monkeypatch.setattr(cfg, "NEWS_MAX_ARTICLES_PER_DAY", 1)
    insert_artifact(service_id="svc-a", url=None, channel="brief", content="a")

    result = select_to_compose_for_day("2026-08-26")
    assert result["platform_slots_available"] == 0
    assert result["platform_slots_filled"] == 0


@pytest.mark.usefixtures("fake_artifact_session")
def test_pin_for_tomorrow_uses_day_after_today() -> None:
    """pin_for_tomorrow resolves "tomorrow" relative to the given `today` and pins for that exact compose day."""
    from algorand_shared.artifact_store import insert_artifact
    from algorand_shared.to_compose_selection import (
        pin_for_tomorrow,
        select_to_compose_for_day,
    )

    artifact_id, _ = insert_artifact(service_id="svc-a", url=None, channel="brief", content="a")
    ok = pin_for_tomorrow(artifact_id, today=dt.date(2026, 8, 25))

    assert ok is True
    result = select_to_compose_for_day("2026-08-26")
    assert result["human_picked"] is True


# --------------------------------------------------------------------------- #
# preview_to_compose_for_day -- the read-only twin of select_to_compose_for_day
# --------------------------------------------------------------------------- #


@pytest.mark.usefixtures("fake_artifact_session")
def test_preview_never_mutates_pending_artifacts_or_to_compose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unlike select_to_compose_for_day, preview must leave every artifact PENDING and never write a to_compose row -- it's dispatched on every admin page load/poll, so it must be safe to call repeatedly."""
    monkeypatch.setattr(
        "app.modules.crawler.ecosystem_sync.ecosystem_listed_domains", lambda: frozenset()
    )
    from algorand_shared.artifact_store import (
        PENDING,
        insert_artifact,
        list_pending_artifacts,
    )
    from algorand_shared.to_compose_selection import (
        list_to_compose_for_day,
        preview_to_compose_for_day,
    )

    insert_artifact(service_id="svc-a", url=None, channel="brief", content="a")
    insert_artifact(service_id="svc-b", url=None, channel="brief", content="b")

    preview_to_compose_for_day("2026-08-26")
    preview_to_compose_for_day("2026-08-26")  # calling it twice must not change anything either

    assert {a.status for a in list_pending_artifacts()} == {PENDING}
    assert len(list_pending_artifacts()) == 2
    assert list_to_compose_for_day("2026-08-26") == []


@pytest.mark.usefixtures("fake_artifact_session")
def test_preview_matches_what_select_would_actually_pick(monkeypatch: pytest.MonkeyPatch) -> None:
    """The lanes preview_to_compose_for_day assigns must agree with a real select_to_compose_for_day run over the same data -- the whole point of the preview is to be trustworthy."""
    monkeypatch.setattr(
        "app.modules.crawler.ecosystem_sync.ecosystem_listed_domains", lambda: frozenset()
    )
    from algorand_shared.artifact_store import (
        insert_artifact,
        pin_artifact_for_day,
        update_artifact_priority,
    )
    from algorand_shared.to_compose_selection import (
        list_to_compose_for_day,
        preview_to_compose_for_day,
        select_to_compose_for_day,
    )
    from app.core import config as cfg

    monkeypatch.setattr(cfg, "NEWS_MAX_ARTICLES_PER_DAY", 3)  # N=3 -> 2 platform slots

    human_id, _ = insert_artifact(service_id="svc-human", url=None, channel="brief", content="pinned")
    pin_artifact_for_day(human_id, "2026-08-26")
    ids = [human_id]
    for i, prio in enumerate([9.0, 5.0, 1.0]):
        aid, _ = insert_artifact(
            service_id=f"svc-{i}", url=None, channel="brief", content=f"content {i}"
        )
        update_artifact_priority(aid, prio)
        ids.append(aid)

    preview = preview_to_compose_for_day("2026-08-26")
    preview_lanes = {item["artifact_id"]: item["selected_lane"] for item in preview["items"]}

    real = select_to_compose_for_day("2026-08-26")
    real_rows = list_to_compose_for_day("2026-08-26")
    real_lanes = {row["artifact_id"]: row["lane"] for row in real_rows}

    assert preview["human_picked"] == real["human_picked"]
    assert preview["platform_slots_filled"] == real["platform_slots_filled"]
    for artifact_id, lane in real_lanes.items():
        assert preview_lanes[artifact_id] == lane
    # Everything NOT selected by the real run must show no lane in the preview.
    unselected = [aid for aid in ids if aid not in real_lanes]
    assert unselected  # sanity: N=3 leaves at least one of these 4 artifacts unpicked
    for artifact_id in unselected:
        assert preview_lanes[artifact_id] is None


@pytest.mark.usefixtures("fake_artifact_session")
def test_preview_includes_priority_breakdown_and_title(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each preview item carries its title (from artifact_content) and all four named score components, summing to the reported total priority."""
    monkeypatch.setattr(
        "app.modules.crawler.ecosystem_sync.ecosystem_listed_domains", lambda: frozenset()
    )
    from algorand_shared.artifact_store import insert_artifact
    from algorand_shared.to_compose_selection import preview_to_compose_for_day

    insert_artifact(
        service_id="svc-a", url=None, channel="brief", content="hello world", title="A Title"
    )

    preview = preview_to_compose_for_day("2026-08-26")
    (item,) = preview["items"]

    assert item["title"] == "A Title"
    breakdown = item["priority_breakdown"]
    assert set(breakdown) == {"word_count", "timeliness", "ecosystem_listed", "skip_count"}
    assert item["priority"] == pytest.approx(sum(breakdown.values()), abs=0.0001)
    # This process (workers) has ecosystem_listed_score's real deps, so the
    # preview never needs to flag itself as degraded.
    assert "ecosystem_scoring_unavailable" not in preview


@pytest.mark.usefixtures("fake_artifact_session")
def test_preview_flags_ecosystem_scoring_unavailable_when_deps_unimportable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simulates a backend-computed preview: ecosystem_listed_score's real crawler/classifier deps genuinely can't be imported, so the top-level payload must say so instead of letting every item's `ecosystem_listed: 0.0` read as a real measured zero."""
    import sys

    from algorand_shared.artifact_store import insert_artifact
    from algorand_shared.to_compose_selection import preview_to_compose_for_day

    insert_artifact(service_id="svc-a", url=None, channel="brief", content="hello world")

    monkeypatch.setitem(sys.modules, "app.modules.crawler.domain_tracker", None)
    preview = preview_to_compose_for_day("2026-08-26")

    assert preview["ecosystem_scoring_unavailable"] is True
    (item,) = preview["items"]
    assert item["priority_breakdown"]["ecosystem_listed"] == 0.0


@pytest.mark.usefixtures("fake_artifact_session")
def test_preview_flags_the_pin_for_the_requested_day_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """is_pinned_for_day is true only for the artifact pinned for THIS specific day, not for a pin set for some other day."""
    monkeypatch.setattr(
        "app.modules.crawler.ecosystem_sync.ecosystem_listed_domains", lambda: frozenset()
    )
    from algorand_shared.artifact_store import insert_artifact, pin_artifact_for_day
    from algorand_shared.to_compose_selection import preview_to_compose_for_day

    picked_id, _ = insert_artifact(service_id="svc-a", url=None, channel="brief", content="a")
    pin_artifact_for_day(picked_id, "2026-08-26")
    other_id, _ = insert_artifact(service_id="svc-b", url=None, channel="brief", content="b")
    pin_artifact_for_day(other_id, "2026-09-01")

    preview = preview_to_compose_for_day("2026-08-26")
    flags = {item["artifact_id"]: item["is_pinned_for_day"] for item in preview["items"]}

    assert flags[picked_id] is True
    assert flags[other_id] is False


# --------------------------------------------------------------------------- #
# Guaranteed new-service platform lane (2026-08-26) -- _rank_platform_picks
# splits eligible platform candidates into NEW_SERVICE_POOL (never-covered
# services) and UPDATE_POOL (already-covered services), each with a
# guaranteed minimum floor, backfilling from the other pool when one is thin,
# with any surplus (uneven floor rounding, or leftover after both floors are
# met) going to the next-highest-priority remaining candidate from EITHER
# pool.
# --------------------------------------------------------------------------- #


def _mock_coverage(monkeypatch: pytest.MonkeyPatch, covered: set[str]) -> None:
    """Make service_has_article return True only for service_ids in `covered` -- everything else reads as a never-covered (new_service pool) service."""
    monkeypatch.setattr(
        "algorand_shared.article_matching.service_has_article",
        lambda sid: sid in covered,
    )


@pytest.mark.usefixtures("fake_artifact_session")
def test_new_service_detection_reflects_service_has_article(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pool label itself: an artifact whose service_id has a prior published article is 'update', one that doesn't is 'new_service' -- the exact signal preview surfaces per item."""
    _mock_coverage(monkeypatch, covered={"svc-covered"})
    from algorand_shared.artifact_store import insert_artifact
    from algorand_shared.to_compose_selection import preview_to_compose_for_day

    insert_artifact(service_id="svc-covered", url=None, channel="crawler", content="update diff")
    insert_artifact(service_id="svc-fresh", url=None, channel="crawler", content="first ever diff")

    preview = preview_to_compose_for_day("2026-08-26")
    pools = {item["service_id"]: item["pool"] for item in preview["items"]}

    assert pools["svc-covered"] == "update"
    assert pools["svc-fresh"] == "new_service"


@pytest.mark.usefixtures("fake_artifact_session")
def test_pool_detection_prefers_venue_service_id_over_literal_service_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bug-class-2 fix: a per-item lane artifact (forum-topic:<id>, one per hot thread) whose literal service_id has never itself been published must still read as 'update' when its VENUE (the forum) has -- otherwise every single item from that lane would permanently occupy the guaranteed new-service floor even though the venue is well covered."""
    _mock_coverage(monkeypatch, covered={"algorand-forum"})
    from algorand_shared.artifact_store import insert_artifact
    from algorand_shared.to_compose_selection import preview_to_compose_for_day

    insert_artifact(
        service_id="forum-topic:15288",
        url=None,
        channel="forum",
        content="hot topic",
        venue_service_id="algorand-forum",
    )

    preview = preview_to_compose_for_day("2026-08-26")
    (item,) = preview["items"]

    assert item["service_id"] == "forum-topic:15288"
    assert item["pool"] == "update"


@pytest.mark.usefixtures("fake_artifact_session")
def test_pool_detection_falls_back_to_service_id_when_no_venue_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An artifact with no venue_service_id (a plain web crawl diff) is unaffected by the fix -- pool classification still keys on its own literal service_id, exactly as before."""
    _mock_coverage(monkeypatch, covered={"svc-covered"})
    from algorand_shared.artifact_store import insert_artifact
    from algorand_shared.to_compose_selection import preview_to_compose_for_day

    insert_artifact(service_id="svc-covered", url=None, channel="crawler", content="update diff")
    insert_artifact(service_id="svc-fresh", url=None, channel="crawler", content="first ever diff")

    preview = preview_to_compose_for_day("2026-08-26")
    pools = {item["service_id"]: item["pool"] for item in preview["items"]}

    assert pools["svc-covered"] == "update"
    assert pools["svc-fresh"] == "new_service"


@pytest.mark.usefixtures("fake_artifact_session")
def test_rank_platform_picks_dedup_still_keys_on_literal_service_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit non-goal: the venue fix changes POOL classification only, never the 1-pending-per-service dedup in _rank_platform_picks, which must stay keyed on the literal per-item service_id -- two different forum threads sharing the same venue are still two distinct dedup candidates, both eligible for a slot."""
    _mock_coverage(monkeypatch, covered={"algorand-forum"})
    from algorand_shared.artifact_store import insert_artifact, update_artifact_priority
    from algorand_shared.to_compose_selection import select_to_compose_for_day
    from app.core import config as cfg

    monkeypatch.setattr(cfg, "NEWS_MAX_ARTICLES_PER_DAY", 3)  # platform_n = 2

    id_a, _ = insert_artifact(
        service_id="forum-topic:1",
        url=None,
        channel="forum",
        content="topic one",
        venue_service_id="algorand-forum",
    )
    id_b, _ = insert_artifact(
        service_id="forum-topic:2",
        url=None,
        channel="forum",
        content="topic two",
        venue_service_id="algorand-forum",
    )
    update_artifact_priority(id_a, 5.0)
    update_artifact_priority(id_b, 4.0)

    result = select_to_compose_for_day("2026-08-26")

    assert result["platform_slots_filled"] == 2
    chosen = {sel["artifact_id"] for sel in result["selections"]}
    assert chosen == {id_a, id_b}


@pytest.mark.usefixtures("fake_artifact_session")
def test_new_service_pool_gets_its_guaranteed_floor_even_at_lower_priority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The core guarantee: with plenty of candidates in both pools, the new-service pool still gets its floor share of platform slots even when every new-service candidate is lower priority than every update candidate -- otherwise a saturating established service would starve new-service coverage entirely."""
    from algorand_shared.artifact_store import insert_artifact, update_artifact_priority
    from algorand_shared.to_compose_selection import select_to_compose_for_day
    from app.core import config as cfg

    monkeypatch.setattr(cfg, "NEWS_MAX_ARTICLES_PER_DAY", 5)  # platform_n = 4
    monkeypatch.setattr(cfg, "ARTIFACT_NEW_SERVICE_MIN_SHARE", 0.5)  # floor 2 / 2

    covered = {f"svc-old-{i}" for i in range(4)}
    _mock_coverage(monkeypatch, covered=covered)

    new_ids = []
    for i, prio in enumerate([1.0, 2.0]):  # LOW priority, never-covered services
        aid, _ = insert_artifact(
            service_id=f"svc-new-{i}", url=None, channel="crawler", content=f"new {i}"
        )
        update_artifact_priority(aid, prio)
        new_ids.append(aid)

    update_ids = []
    for i, prio in enumerate([9.0, 8.0, 7.0, 6.0]):  # HIGH priority, already-covered services
        aid, _ = insert_artifact(
            service_id=f"svc-old-{i}", url=None, channel="crawler", content=f"old {i}"
        )
        update_artifact_priority(aid, prio)
        update_ids.append(aid)

    result = select_to_compose_for_day("2026-08-26")

    assert result["platform_slots_filled"] == 4
    assert result["platform_pool_counts"] == {"new_service": 2, "update": 2}
    chosen = {sel["artifact_id"] for sel in result["selections"]}
    # Both new-service artifacts made it in despite being lower priority than
    # every update candidate.
    assert set(new_ids) <= chosen
    # Only the TOP 2 update candidates (by priority) made it in -- the floor
    # protects new-service slots from being crowded out.
    assert update_ids[0] in chosen  # 9.0
    assert update_ids[1] in chosen  # 8.0
    assert update_ids[2] not in chosen  # 7.0
    assert update_ids[3] not in chosen  # 6.0


@pytest.mark.usefixtures("fake_artifact_session")
def test_thin_new_service_pool_is_backfilled_from_update_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the new-service pool doesn't have enough eligible candidates to fill its own floor, the leftover slot(s) backfill from the update pool rather than leaving a platform slot empty."""
    from algorand_shared.artifact_store import insert_artifact, update_artifact_priority
    from algorand_shared.to_compose_selection import select_to_compose_for_day
    from app.core import config as cfg

    monkeypatch.setattr(cfg, "NEWS_MAX_ARTICLES_PER_DAY", 5)  # platform_n = 4
    monkeypatch.setattr(cfg, "ARTIFACT_NEW_SERVICE_MIN_SHARE", 0.5)  # floor 2 / 2

    covered = {f"svc-old-{i}" for i in range(5)}
    _mock_coverage(monkeypatch, covered=covered)

    # Only ONE new-service candidate exists -- the pool is thin relative to
    # its floor of 2.
    only_new_id, _ = insert_artifact(
        service_id="svc-new-0", url=None, channel="crawler", content="the only new one"
    )
    update_artifact_priority(only_new_id, 1.0)

    update_ids = []
    for i, prio in enumerate([9.0, 8.0, 7.0, 6.0, 5.0]):
        aid, _ = insert_artifact(
            service_id=f"svc-old-{i}", url=None, channel="crawler", content=f"old {i}"
        )
        update_artifact_priority(aid, prio)
        update_ids.append(aid)

    result = select_to_compose_for_day("2026-08-26")

    # All 4 platform slots filled despite the new-service pool only having 1
    # candidate -- no slot left empty for lack of new-service candidates.
    assert result["platform_slots_filled"] == 4
    assert result["platform_pool_counts"] == {"new_service": 1, "update": 3}
    chosen = {sel["artifact_id"] for sel in result["selections"]}
    assert only_new_id in chosen
    # The top 3 update candidates fill the rest (1 floor slot + 2 backfilled).
    assert set(update_ids[:3]) <= chosen
    assert update_ids[3] not in chosen
    assert update_ids[4] not in chosen


@pytest.mark.usefixtures("fake_artifact_session")
def test_thin_update_pool_is_backfilled_from_new_service_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Symmetric to the new-service-pool-thin case: when the UPDATE pool is what's thin, its shortfall backfills from the new-service pool instead."""
    from algorand_shared.artifact_store import insert_artifact, update_artifact_priority
    from algorand_shared.to_compose_selection import select_to_compose_for_day
    from app.core import config as cfg

    monkeypatch.setattr(cfg, "NEWS_MAX_ARTICLES_PER_DAY", 5)  # platform_n = 4
    monkeypatch.setattr(cfg, "ARTIFACT_NEW_SERVICE_MIN_SHARE", 0.5)  # floor 2 / 2
    _mock_coverage(monkeypatch, covered=set())  # nothing is covered -> update pool is EMPTY

    new_ids = []
    for i, prio in enumerate([9.0, 8.0, 7.0, 6.0]):
        aid, _ = insert_artifact(
            service_id=f"svc-new-{i}", url=None, channel="crawler", content=f"new {i}"
        )
        update_artifact_priority(aid, prio)
        new_ids.append(aid)

    result = select_to_compose_for_day("2026-08-26")

    assert result["platform_slots_filled"] == 4
    assert result["platform_pool_counts"] == {"new_service": 4, "update": 0}
    chosen = {sel["artifact_id"] for sel in result["selections"]}
    assert set(new_ids) == chosen


@pytest.mark.usefixtures("fake_artifact_session")
def test_surplus_slot_beyond_the_floor_goes_to_next_highest_priority_regardless_of_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Floors are a MINIMUM, not a partition: once both pools' floors are satisfied, a leftover slot (here, from odd platform_n not dividing evenly) goes to whichever pool has the next-highest-priority remaining candidate -- an already-covered service's second-best update can still win it over a weak new-service candidate. This is the explicit owner carve-out: "if some project did a big rework, the big rework would probably [earn] priority"."""
    from algorand_shared.artifact_store import insert_artifact, update_artifact_priority
    from algorand_shared.to_compose_selection import select_to_compose_for_day
    from app.core import config as cfg

    monkeypatch.setattr(cfg, "NEWS_MAX_ARTICLES_PER_DAY", 4)  # platform_n = 3 (odd -> 1 surplus)
    monkeypatch.setattr(cfg, "ARTIFACT_NEW_SERVICE_MIN_SHARE", 0.5)  # floor 1 / 1
    _mock_coverage(monkeypatch, covered={"svc-old-a", "svc-old-b"})

    # A single, LOW-priority new-service candidate -- it still claims the
    # guaranteed floor slot.
    new_id, _ = insert_artifact(
        service_id="svc-new-only", url=None, channel="crawler", content="new"
    )
    update_artifact_priority(new_id, 1.0)

    # Two HIGH-priority update candidates -- the top one claims the update
    # floor; the second one should win the surplus slot over nothing else
    # being available in the new pool.
    old_a_id, _ = insert_artifact(service_id="svc-old-a", url=None, channel="crawler", content="a")
    update_artifact_priority(old_a_id, 10.0)
    old_b_id, _ = insert_artifact(service_id="svc-old-b", url=None, channel="crawler", content="b")
    update_artifact_priority(old_b_id, 9.0)

    result = select_to_compose_for_day("2026-08-26")

    assert result["platform_slots_filled"] == 3
    # The floor guarantees exactly 1 new-service slot; the surplus slot goes
    # to the update pool's SECOND artifact (next-highest priority overall),
    # not forced into the (exhausted) new-service pool.
    assert result["platform_pool_counts"] == {"new_service": 1, "update": 2}
    chosen = {sel["artifact_id"] for sel in result["selections"]}
    assert chosen == {new_id, old_a_id, old_b_id}


# --------------------------------------------------------------------------- #
# reset_to_compose_for_day / reset_and_reselect_for_day (2026-08-26) -- the
# "redo today's picks" admin action: clear a day's locked-in to_compose
# selection, revert any still-SELECTED artifact it picked back to pending,
# and (for the combined function) immediately re-run selection.
# --------------------------------------------------------------------------- #


@pytest.mark.usefixtures("fake_artifact_session")
def test_reset_clears_to_compose_rows_for_the_day() -> None:
    """After a reset, the day's to_compose partition is fully empty."""
    from algorand_shared.artifact_store import insert_artifact
    from algorand_shared.to_compose_selection import (
        list_to_compose_for_day,
        reset_to_compose_for_day,
        select_to_compose_for_day,
    )

    insert_artifact(service_id="svc-a", url=None, channel="brief", content="a")
    insert_artifact(service_id="svc-b", url=None, channel="brief", content="b")
    select_to_compose_for_day("2026-08-26")
    assert list_to_compose_for_day("2026-08-26") != []

    reset_to_compose_for_day("2026-08-26")

    assert list_to_compose_for_day("2026-08-26") == []


@pytest.mark.usefixtures("fake_artifact_session")
def test_reset_reverts_still_selected_artifacts_to_pending(
    fake_artifact_session: FakeArtifactSession,
) -> None:
    """Every artifact a prior select_to_compose_for_day run picked (and left SELECTED) goes back to PENDING and is reported in reverted_to_pending."""
    from algorand_shared.artifact_store import PENDING, SELECTED, insert_artifact
    from algorand_shared.to_compose_selection import (
        reset_to_compose_for_day,
        select_to_compose_for_day,
    )

    aid, _ = insert_artifact(service_id="svc-a", url=None, channel="brief", content="a")
    select_to_compose_for_day("2026-08-26")
    assert fake_artifact_session.artifacts[aid]["status"] == SELECTED

    result = reset_to_compose_for_day("2026-08-26")

    assert result["reverted_to_pending"] == [aid]
    assert result["skipped"] == []
    assert result["fully_reverted"] is True
    assert fake_artifact_session.artifacts[aid]["status"] == PENDING
    pending_ids = {str(r["artifact_id"]) for r in fake_artifact_session.pending.values()}
    assert aid in pending_ids


@pytest.mark.usefixtures("fake_artifact_session")
def test_reset_does_not_revert_an_already_composed_artifact(
    fake_artifact_session: FakeArtifactSession,
) -> None:
    """An artifact that progressed past SELECTED to COMPOSED (drain_to_compose already ran) between the original selection and the reset must be left alone -- reported in `skipped`, never resurrected."""
    from algorand_shared.artifact_store import (
        COMPOSED,
        insert_artifact,
        mark_artifact_status,
    )
    from algorand_shared.to_compose_selection import (
        reset_to_compose_for_day,
        select_to_compose_for_day,
    )

    aid, _ = insert_artifact(service_id="svc-a", url=None, channel="brief", content="a")
    select_to_compose_for_day("2026-08-26")
    mark_artifact_status(aid, COMPOSED)  # simulates drain_to_compose already having run

    result = reset_to_compose_for_day("2026-08-26")

    assert result["reverted_to_pending"] == []
    assert result["skipped"] == [{"artifact_id": aid, "status": COMPOSED}]
    assert result["fully_reverted"] is False
    assert fake_artifact_session.artifacts[aid]["status"] == COMPOSED
    assert fake_artifact_session.pending == {}


@pytest.mark.usefixtures("fake_artifact_session")
def test_reset_does_not_revert_an_already_discarded_artifact(
    fake_artifact_session: FakeArtifactSession,
) -> None:
    """Symmetric to the composed case: an artifact a pre-compose gate permanently DISCARDED after selection is also left alone, not resurrected."""
    from algorand_shared.artifact_store import (
        DISCARDED,
        insert_artifact,
        mark_artifact_status,
    )
    from algorand_shared.to_compose_selection import (
        reset_to_compose_for_day,
        select_to_compose_for_day,
    )

    aid, _ = insert_artifact(service_id="svc-a", url=None, channel="brief", content="a")
    select_to_compose_for_day("2026-08-26")
    mark_artifact_status(aid, DISCARDED)

    result = reset_to_compose_for_day("2026-08-26")

    assert result["skipped"] == [{"artifact_id": aid, "status": DISCARDED}]
    assert fake_artifact_session.artifacts[aid]["status"] == DISCARDED


@pytest.mark.usefixtures("fake_artifact_session")
def test_reset_still_clears_to_compose_even_when_a_pick_was_skipped() -> None:
    """The to_compose rows are cleared unconditionally -- a skipped (already-progressed) artifact only blocks its OWN status revert, not the partition clear."""
    from algorand_shared.artifact_store import COMPOSED, insert_artifact, mark_artifact_status
    from algorand_shared.to_compose_selection import (
        list_to_compose_for_day,
        reset_to_compose_for_day,
        select_to_compose_for_day,
    )

    aid, _ = insert_artifact(service_id="svc-a", url=None, channel="brief", content="a")
    select_to_compose_for_day("2026-08-26")
    mark_artifact_status(aid, COMPOSED)

    reset_to_compose_for_day("2026-08-26")

    assert list_to_compose_for_day("2026-08-26") == []


@pytest.mark.usefixtures("fake_artifact_session")
def test_reset_on_a_day_with_nothing_selected_is_a_clean_noop(
    fake_artifact_session: FakeArtifactSession,  # noqa: ARG001 -- activates the fixture's monkeypatch
) -> None:
    """Resetting a day that was never selected (an empty to_compose partition) doesn't error -- zero cleared, zero reverted, fully_reverted True."""
    from algorand_shared.to_compose_selection import reset_to_compose_for_day

    result = reset_to_compose_for_day("2026-08-26")

    assert result["cleared_slots"] == 0
    assert result["reverted_to_pending"] == []
    assert result["skipped"] == []
    assert result["fully_reverted"] is True


@pytest.mark.usefixtures("fake_artifact_session")
def test_reset_and_reselect_produces_a_fresh_valid_selection(
    fake_artifact_session: FakeArtifactSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The combined one-button action: after reset_and_reselect_for_day, the day has a brand-new to_compose lineup drawn from the widened (reverted) pending pool -- exercising the idempotency the module docstring calls out (clearing to_compose first is what makes a second select_to_compose_for_day call for the same day safe)."""
    from algorand_shared.artifact_store import (
        SELECTED,
        insert_artifact,
        update_artifact_priority,
    )
    from algorand_shared.to_compose_selection import (
        list_to_compose_for_day,
        reset_and_reselect_for_day,
        select_to_compose_for_day,
    )
    from app.core import config as cfg

    monkeypatch.setattr(cfg, "NEWS_MAX_ARTICLES_PER_DAY", 2)

    low_id, _ = insert_artifact(service_id="svc-low", url=None, channel="brief", content="low")
    update_artifact_priority(low_id, 1.0)
    select_to_compose_for_day("2026-08-26")
    first_rows = list_to_compose_for_day("2026-08-26")
    assert [r["artifact_id"] for r in first_rows] == [low_id]
    assert fake_artifact_session.artifacts[low_id]["status"] == SELECTED

    # A higher-priority artifact shows up only AFTER the reset widens the
    # pool back out (it was inserted before the reset but couldn't have won
    # the already-locked-in slot).
    high_id, _ = insert_artifact(service_id="svc-high", url=None, channel="brief", content="high")
    update_artifact_priority(high_id, 9.0)

    result = reset_and_reselect_for_day("2026-08-26")

    assert result["reset"]["reverted_to_pending"] == [low_id]
    rows = list_to_compose_for_day("2026-08-26")
    assert [r["artifact_id"] for r in rows] == [high_id]
    assert fake_artifact_session.artifacts[high_id]["status"] == SELECTED
    # The reverted artifact is genuinely back in play (pending), just lost
    # this round to the higher-priority newcomer.
    assert fake_artifact_session.artifacts[low_id]["status"] == "pending"


@pytest.mark.usefixtures("fake_artifact_session")
def test_preview_pool_field_present_for_every_pending_item_not_just_selected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """preview_to_compose_for_day tags EVERY pending item with its pool, not only the ones it would select -- an admin dashboard wants to see why a low-priority new-service artifact is waiting, not just what won."""
    monkeypatch.setattr(
        "app.modules.crawler.ecosystem_sync.ecosystem_listed_domains", lambda: frozenset()
    )
    from algorand_shared.artifact_store import insert_artifact, update_artifact_priority
    from algorand_shared.to_compose_selection import preview_to_compose_for_day
    from app.core import config as cfg

    monkeypatch.setattr(cfg, "NEWS_MAX_ARTICLES_PER_DAY", 2)  # platform_n = 1
    _mock_coverage(monkeypatch, covered={"svc-covered"})

    covered_id, _ = insert_artifact(service_id="svc-covered", url=None, channel="crawler", content="a")
    update_artifact_priority(covered_id, 5.0)
    unselected_id, _ = insert_artifact(
        service_id="svc-fresh", url=None, channel="crawler", content="b"
    )
    update_artifact_priority(unselected_id, 0.0)  # lower priority -> loses the single platform slot

    preview = preview_to_compose_for_day("2026-08-26")
    by_service = {item["service_id"]: item for item in preview["items"]}

    assert by_service["svc-covered"]["pool"] == "update"
    assert by_service["svc-fresh"]["pool"] == "new_service"
    # Even the artifact this preview would NOT select still carries its pool.
    assert by_service["svc-fresh"]["selected_lane"] is None


@pytest.mark.usefixtures("fake_artifact_session")
def test_find_stale_selected_finds_a_platform_pick_stuck_past_its_day(
    fake_artifact_session: FakeArtifactSession,
) -> None:
    """A platform pick selected for a day that has since passed, still SELECTED (drain_to_compose never reached it), is reported stale -- the real 2026-08-25 casualty this function was root-caused from."""
    from algorand_shared.artifact_store import SELECTED, insert_artifact
    from algorand_shared.to_compose_selection import (
        find_stale_selected_artifacts,
        select_to_compose_for_day,
    )

    aid, _ = insert_artifact(service_id="svc-a", url=None, channel="brief", content="a")
    select_to_compose_for_day("2026-08-25")
    assert fake_artifact_session.artifacts[aid]["status"] == SELECTED

    stale = find_stale_selected_artifacts(today="2026-08-26")

    assert len(stale) == 1
    assert stale[0]["artifact_id"] == aid
    assert stale[0]["compose_day"] == "2026-08-25"
    assert stale[0]["slot"] == 0
    assert stale[0]["lane"] == "platform"


@pytest.mark.usefixtures("fake_artifact_session")
def test_find_stale_selected_ignores_todays_own_selection() -> None:
    """A pick selected for TODAY is never flagged stale -- drain_to_compose may still legitimately reach it later the same day."""
    from algorand_shared.artifact_store import insert_artifact
    from algorand_shared.to_compose_selection import (
        find_stale_selected_artifacts,
        select_to_compose_for_day,
    )

    insert_artifact(service_id="svc-a", url=None, channel="brief", content="a")
    select_to_compose_for_day("2026-08-26")

    assert find_stale_selected_artifacts(today="2026-08-26") == []


@pytest.mark.usefixtures("fake_artifact_session")
def test_find_stale_selected_ignores_already_composed_or_discarded() -> None:
    """A past-day pick that already progressed past SELECTED (composed or discarded by drain_to_compose before the day ended) is not stale -- it resolved, it just didn't get stuck."""
    from algorand_shared.artifact_store import (
        COMPOSED,
        DISCARDED,
        insert_artifact,
        mark_artifact_status,
    )
    from algorand_shared.to_compose_selection import (
        find_stale_selected_artifacts,
        select_to_compose_for_day,
    )

    composed_id, _ = insert_artifact(service_id="svc-composed", url=None, channel="brief", content="a")
    discarded_id, _ = insert_artifact(service_id="svc-discarded", url=None, channel="brief", content="b")
    select_to_compose_for_day("2026-08-25")
    mark_artifact_status(composed_id, COMPOSED)
    mark_artifact_status(discarded_id, DISCARDED)

    assert find_stale_selected_artifacts(today="2026-08-26") == []


@pytest.mark.usefixtures("fake_artifact_session")
def test_reclaim_stale_selected_dry_run_makes_no_writes(
    fake_artifact_session: FakeArtifactSession,
) -> None:
    """dry_run=True (the default) reports what would be reclaimed without touching artifact status or the pending index."""
    from algorand_shared.artifact_store import SELECTED, insert_artifact
    from algorand_shared.to_compose_selection import (
        reclaim_stale_selected_artifacts,
        select_to_compose_for_day,
    )

    aid, _ = insert_artifact(service_id="svc-a", url=None, channel="brief", content="a")
    select_to_compose_for_day("2026-08-25")

    result = reclaim_stale_selected_artifacts(today="2026-08-26", dry_run=True)

    assert result["dry_run"] is True
    assert result["reclaimed_count"] == 1
    assert fake_artifact_session.artifacts[aid]["status"] == SELECTED
    assert fake_artifact_session.pending == {}


@pytest.mark.usefixtures("fake_artifact_session")
def test_reclaim_stale_selected_reverts_platform_pick_to_pending(
    fake_artifact_session: FakeArtifactSession,
) -> None:
    """A real (dry_run=False) reclaim moves the artifact back to PENDING and re-adds it to the pending index, so a future select_to_compose_for_day can pick it up again like any other candidate."""
    from algorand_shared.artifact_store import PENDING, insert_artifact
    from algorand_shared.to_compose_selection import (
        reclaim_stale_selected_artifacts,
        select_to_compose_for_day,
    )

    aid, _ = insert_artifact(service_id="svc-a", url=None, channel="brief", content="a")
    select_to_compose_for_day("2026-08-25")

    result = reclaim_stale_selected_artifacts(today="2026-08-26", dry_run=False)

    assert result["reclaimed_count"] == 1
    assert fake_artifact_session.artifacts[aid]["status"] == PENDING
    pending_ids = {str(r["artifact_id"]) for r in fake_artifact_session.pending.values()}
    assert aid in pending_ids


@pytest.mark.usefixtures("fake_artifact_session")
def test_reclaim_stale_selected_deletes_the_stale_slots_own_to_compose_row() -> None:
    """A real reclaim also clears the reclaimed artifact's own (compose_day, slot) row -- otherwise the admin "Selected for {day}" view (which reads to_compose directly) keeps showing an artifact as still locked in for a day that will now never compose it, even though its live status has already moved back to pending."""
    from algorand_shared.artifact_store import insert_artifact
    from algorand_shared.to_compose_selection import (
        list_to_compose_for_day,
        reclaim_stale_selected_artifacts,
        select_to_compose_for_day,
    )

    insert_artifact(service_id="svc-a", url=None, channel="brief", content="a")
    select_to_compose_for_day("2026-08-25")
    assert list_to_compose_for_day("2026-08-25") != []

    result = reclaim_stale_selected_artifacts(today="2026-08-26", dry_run=False)

    assert result["reclaimed_count"] == 1
    assert list_to_compose_for_day("2026-08-25") == []


@pytest.mark.usefixtures("fake_artifact_session")
def test_reclaim_stale_selected_dry_run_leaves_the_to_compose_row_untouched() -> None:
    """dry_run=True must not delete anything from to_compose either -- it's a pure report."""
    from algorand_shared.artifact_store import insert_artifact
    from algorand_shared.to_compose_selection import (
        list_to_compose_for_day,
        reclaim_stale_selected_artifacts,
        select_to_compose_for_day,
    )

    insert_artifact(service_id="svc-a", url=None, channel="brief", content="a")
    select_to_compose_for_day("2026-08-25")

    reclaim_stale_selected_artifacts(today="2026-08-26", dry_run=True)

    assert list_to_compose_for_day("2026-08-25") != []


@pytest.mark.usefixtures("fake_artifact_session")
def test_reclaim_stale_selected_only_deletes_the_reclaimed_slot_not_the_whole_day() -> None:
    """A stale slot's deletion must never touch a SIBLING slot for the same day -- e.g. a human pick reclaimed alongside a platform pick that's already progressed to COMPOSED must leave the composed slot's own to_compose row alone."""
    from algorand_shared.artifact_store import COMPOSED, insert_artifact, mark_artifact_status
    from algorand_shared.to_compose_selection import (
        list_to_compose_for_day,
        pin_for_tomorrow,
        reclaim_stale_selected_artifacts,
        select_to_compose_for_day,
    )

    human_id, _ = insert_artifact(service_id="svc-human", url=None, channel="brief", content="a")
    platform_id, _ = insert_artifact(service_id="svc-platform", url=None, channel="brief", content="b")
    pin_for_tomorrow(human_id, today=dt.date(2026, 8, 24))
    select_to_compose_for_day("2026-08-25")
    mark_artifact_status(platform_id, COMPOSED)  # the platform slot already composed successfully

    result = reclaim_stale_selected_artifacts(today="2026-08-26", dry_run=False)

    assert result["reclaimed_count"] == 1  # only the still-stuck human pick
    remaining = list_to_compose_for_day("2026-08-25")
    assert len(remaining) == 1
    assert remaining[0]["artifact_id"] == platform_id


@pytest.mark.usefixtures("fake_artifact_session")
def test_reclaim_stale_selected_repins_a_stranded_human_pick_forward(
    fake_artifact_session: FakeArtifactSession,
) -> None:
    """2026-08-28: a stranded HUMAN pick is carried forward -- re-pinned for the next actionable day -- not reverted to plain pending with its pin cleared. The human's specific choice must survive, not be dumped back into the undifferentiated pool."""
    from algorand_shared.artifact_store import PENDING, insert_artifact, pin_artifact_for_day
    from algorand_shared.to_compose_selection import (
        reclaim_stale_selected_artifacts,
        select_to_compose_for_day,
    )

    aid, _ = insert_artifact(service_id="svc-a", url=None, channel="brief", content="a")
    pin_artifact_for_day(aid, "2026-08-25")
    select_to_compose_for_day("2026-08-25")

    result = reclaim_stale_selected_artifacts(today="2026-08-26", dry_run=False)

    assert result["reclaimed_count"] == 1
    assert result["reclaimed"][0]["action"] == "repinned_forward"
    assert result["reclaimed"][0]["target_day"] == "2026-08-26"
    # Reverted to pending (so it's eligible again)...
    assert fake_artifact_session.artifacts[aid]["status"] == PENDING
    # ...but re-pinned for the next actionable day, NOT cleared to None.
    assert fake_artifact_session.artifacts[aid]["human_pick_day"] == "2026-08-26"


@pytest.mark.usefixtures("fake_artifact_session")
def test_reclaim_stale_selected_repin_lands_on_today_when_today_is_open(
    fake_artifact_session: FakeArtifactSession,
) -> None:
    """When `today` itself has no to_compose selection yet, the re-pin targets `today` directly -- eligible the very next time select_to_compose_for_day/select_to_compose_for_today_task actually processes it, not pushed needlessly further out."""
    from algorand_shared.artifact_store import insert_artifact, pin_artifact_for_day
    from algorand_shared.to_compose_selection import (
        reclaim_stale_selected_artifacts,
        select_to_compose_for_day,
    )

    aid, _ = insert_artifact(service_id="svc-a", url=None, channel="brief", content="a")
    pin_artifact_for_day(aid, "2026-08-25")
    select_to_compose_for_day("2026-08-25")
    # Note: nothing has selected "2026-08-26" yet.

    result = reclaim_stale_selected_artifacts(today="2026-08-26", dry_run=False)

    assert result["reclaimed"][0]["target_day"] == "2026-08-26"
    assert fake_artifact_session.artifacts[aid]["human_pick_day"] == "2026-08-26"


@pytest.mark.usefixtures("fake_artifact_session")
def test_reclaim_stale_selected_repin_skips_a_day_already_locked_in(
    fake_artifact_session: FakeArtifactSession,
) -> None:
    """When `today` has already been selected (a real to_compose row exists for it), re-pinning for `today` would never be honored -- select_to_compose_for_today_task is a no-op once a day has any rows. The re-pin must skip forward to the next day that isn't locked in yet."""
    from algorand_shared.artifact_store import insert_artifact, pin_artifact_for_day
    from algorand_shared.to_compose_selection import (
        reclaim_stale_selected_artifacts,
        select_to_compose_for_day,
    )

    stranded_id, _ = insert_artifact(
        service_id="svc-stranded", url=None, channel="brief", content="a"
    )
    pin_artifact_for_day(stranded_id, "2026-08-25")
    select_to_compose_for_day("2026-08-25")

    # Today's own slate has already been locked in by an unrelated pick.
    insert_artifact(service_id="svc-today", url=None, channel="brief", content="b")
    select_to_compose_for_day("2026-08-26")

    result = reclaim_stale_selected_artifacts(today="2026-08-26", dry_run=False)

    assert result["reclaimed"][0]["target_day"] == "2026-08-27"
    assert fake_artifact_session.artifacts[stranded_id]["human_pick_day"] == "2026-08-27"


@pytest.mark.usefixtures("fake_artifact_session")
def test_reclaim_stale_selected_repin_does_not_steal_a_genuine_future_pin(
    fake_artifact_session: FakeArtifactSession,
) -> None:
    """A carried-forward re-pin must never clobber an unrelated, genuine admin pin already waiting for a future day -- it should skip past that day to the next open one instead."""
    from algorand_shared.artifact_store import insert_artifact, pin_artifact_for_day
    from algorand_shared.to_compose_selection import (
        reclaim_stale_selected_artifacts,
        select_to_compose_for_day,
    )

    stranded_id, _ = insert_artifact(
        service_id="svc-stranded", url=None, channel="brief", content="a"
    )
    pin_artifact_for_day(stranded_id, "2026-08-25")
    select_to_compose_for_day("2026-08-25")

    # A genuine, unrelated admin pin already waiting for "today".
    future_pick_id, _ = insert_artifact(
        service_id="svc-future", url=None, channel="brief", content="c"
    )
    pin_artifact_for_day(future_pick_id, "2026-08-26")

    result = reclaim_stale_selected_artifacts(today="2026-08-26", dry_run=False)

    assert result["reclaimed"][0]["target_day"] == "2026-08-27"
    # The genuine future pin is untouched.
    assert fake_artifact_session.artifacts[future_pick_id]["human_pick_day"] == "2026-08-26"


@pytest.mark.usefixtures("fake_artifact_session")
def test_reclaim_stale_selected_two_stranded_human_picks_land_on_different_days(
    fake_artifact_session: FakeArtifactSession,
) -> None:
    """Multi-day-stale scenario: two separate human picks stranded on two different past days (e.g. several consecutive paused days) must each roll forward to a DIFFERENT day, never collide or clobber each other's pin."""
    from algorand_shared.artifact_store import insert_artifact, pin_artifact_for_day
    from algorand_shared.to_compose_selection import (
        list_to_compose_for_day,
        reclaim_stale_selected_artifacts,
        select_to_compose_for_day,
    )

    first_id, _ = insert_artifact(service_id="svc-first", url=None, channel="brief", content="a")
    pin_artifact_for_day(first_id, "2026-08-24")
    select_to_compose_for_day("2026-08-24")

    second_id, _ = insert_artifact(service_id="svc-second", url=None, channel="brief", content="b")
    pin_artifact_for_day(second_id, "2026-08-25")
    select_to_compose_for_day("2026-08-25")

    result = reclaim_stale_selected_artifacts(today="2026-08-26", dry_run=False)

    assert result["reclaimed_count"] == 2
    target_days = {r["target_day"] for r in result["reclaimed"]}
    assert len(target_days) == 2  # never the same day twice
    assert fake_artifact_session.artifacts[first_id]["human_pick_day"] in target_days
    assert fake_artifact_session.artifacts[second_id]["human_pick_day"] in target_days
    assert (
        fake_artifact_session.artifacts[first_id]["human_pick_day"]
        != fake_artifact_session.artifacts[second_id]["human_pick_day"]
    )
    # Neither original stale day's row survives.
    assert list_to_compose_for_day("2026-08-24") == []
    assert list_to_compose_for_day("2026-08-25") == []


@pytest.mark.usefixtures("fake_artifact_session")
def test_reclaim_stale_selected_repin_dry_run_reports_target_day_without_writing(
    fake_artifact_session: FakeArtifactSession,
) -> None:
    """dry_run=True must accurately preview WHICH day a human pick would be re-pinned to, without writing anything -- the whole point of a dry-run report under the new carry-forward design."""
    from algorand_shared.artifact_store import SELECTED, insert_artifact, pin_artifact_for_day
    from algorand_shared.to_compose_selection import (
        reclaim_stale_selected_artifacts,
        select_to_compose_for_day,
    )

    aid, _ = insert_artifact(service_id="svc-a", url=None, channel="brief", content="a")
    pin_artifact_for_day(aid, "2026-08-25")
    select_to_compose_for_day("2026-08-25")

    result = reclaim_stale_selected_artifacts(today="2026-08-26", dry_run=True)

    assert result["reclaimed"][0]["action"] == "repinned_forward"
    assert result["reclaimed"][0]["target_day"] == "2026-08-26"
    # Nothing actually written: still SELECTED, still pinned for the old day.
    assert fake_artifact_session.artifacts[aid]["status"] == SELECTED
    assert fake_artifact_session.artifacts[aid]["human_pick_day"] == "2026-08-25"


@pytest.mark.usefixtures("fake_artifact_session")
def test_reclaim_stale_selected_platform_pick_gets_a_priority_boost(
    fake_artifact_session: FakeArtifactSession,
) -> None:
    """A stranded PLATFORM pick is reverted to pending like before, but now with its priority boosted strictly above the current top of the pending pool -- a real, not just theoretical, near-guarantee of winning a platform slot the very next selection run, since a direct in-place edit of an already-locked day's to_compose row is unsafe (see the function's own docstring)."""
    from algorand_shared.artifact_store import (
        PENDING,
        insert_artifact,
        update_artifact_priority,
    )
    from algorand_shared.to_compose_selection import (
        reclaim_stale_selected_artifacts,
        select_to_compose_for_day,
    )

    stranded_id, _ = insert_artifact(
        service_id="svc-stranded", url=None, channel="brief", content="a"
    )
    select_to_compose_for_day("2026-08-25")  # platform-fills the one platform slot

    other_id, _ = insert_artifact(service_id="svc-other", url=None, channel="brief", content="b")
    update_artifact_priority(other_id, 5.0)  # the current top of the pending pool

    result = reclaim_stale_selected_artifacts(today="2026-08-26", dry_run=False)

    assert result["reclaimed_count"] == 1
    entry = result["reclaimed"][0]
    assert entry["action"] == "reverted_with_priority_boost"
    assert entry["boosted_priority"] > 5.0
    assert fake_artifact_session.artifacts[stranded_id]["status"] == PENDING
    assert fake_artifact_session.artifacts[stranded_id]["priority"] == entry["boosted_priority"]


@pytest.mark.usefixtures("fake_artifact_session")
def test_reclaim_stale_selected_platform_boost_dry_run_makes_no_writes(
    fake_artifact_session: FakeArtifactSession,
) -> None:
    """dry_run=True must report the platform boost plan (action + boosted_priority) without actually reverting status or touching priority."""
    from algorand_shared.artifact_store import SELECTED, insert_artifact
    from algorand_shared.to_compose_selection import (
        reclaim_stale_selected_artifacts,
        select_to_compose_for_day,
    )

    stranded_id, _ = insert_artifact(
        service_id="svc-stranded", url=None, channel="brief", content="a"
    )
    select_to_compose_for_day("2026-08-25")
    original_priority = fake_artifact_session.artifacts[stranded_id]["priority"]

    result = reclaim_stale_selected_artifacts(today="2026-08-26", dry_run=True)

    entry = result["reclaimed"][0]
    assert entry["action"] == "reverted_with_priority_boost"
    assert "boosted_priority" in entry
    assert fake_artifact_session.artifacts[stranded_id]["status"] == SELECTED
    assert fake_artifact_session.artifacts[stranded_id]["priority"] == original_priority


@pytest.mark.usefixtures("fake_artifact_session")
def test_reclaim_stale_selected_human_pick_rolls_forward_across_several_paused_days(
    fake_artifact_session: FakeArtifactSession,
) -> None:
    """End-to-end multi-day-stale simulation: several consecutive days pass with compose paused. Each day's own daily selection beat (unconditional, not gated on the pause) re-selects the still-pinned artifact into that day's human slot; each day's reclaim then carries it forward again. No duplicate/dangling to_compose rows must accumulate across the whole run."""
    from algorand_shared.artifact_store import PENDING, insert_artifact, pin_artifact_for_day
    from algorand_shared.to_compose_selection import (
        list_to_compose_for_day,
        reclaim_stale_selected_artifacts,
        select_to_compose_for_day,
    )

    aid, _ = insert_artifact(service_id="svc-a", url=None, channel="brief", content="a")
    pin_artifact_for_day(aid, "2026-08-24")
    select_to_compose_for_day("2026-08-24")  # day 1: selected, never composed (paused)

    # Day 2 rolls over: reclaim carries it forward to 2026-08-25, then that
    # day's own (unconditional) selection beat locks it in again.
    reclaim_stale_selected_artifacts(today="2026-08-25", dry_run=False)
    assert fake_artifact_session.artifacts[aid]["status"] == PENDING
    select_to_compose_for_day("2026-08-25")
    assert fake_artifact_session.artifacts[aid]["human_pick_day"] == "2026-08-25"

    # Day 3 rolls over: still paused, still never composed -- carried
    # forward again.
    reclaim_stale_selected_artifacts(today="2026-08-26", dry_run=False)
    select_to_compose_for_day("2026-08-26")

    # Only the CURRENT day's row survives -- no duplicate/dangling rows from
    # any of the earlier days.
    assert list_to_compose_for_day("2026-08-24") == []
    assert list_to_compose_for_day("2026-08-25") == []
    current = list_to_compose_for_day("2026-08-26")
    assert len(current) == 1
    assert current[0]["artifact_id"] == aid
    assert current[0]["lane"] == "human"


@pytest.mark.usefixtures("fake_artifact_session")
def test_reclaim_stale_selected_does_not_clear_pin_for_an_unreclaimed_platform_pick() -> None:
    """The clear-pin side effect only ever fires for the human lane -- a reclaimed platform pick (never had a human_pick_day at all) must not trip it."""
    from algorand_shared.artifact_store import insert_artifact
    from algorand_shared.to_compose_selection import (
        reclaim_stale_selected_artifacts,
        select_to_compose_for_day,
    )

    insert_artifact(service_id="svc-a", url=None, channel="brief", content="a")
    select_to_compose_for_day("2026-08-25")

    # No exception, no crash touching a human_pick_day that was never set.
    result = reclaim_stale_selected_artifacts(today="2026-08-26", dry_run=False)
    assert result["reclaimed_count"] == 1


@pytest.mark.usefixtures("fake_artifact_session")
def test_selecting_an_artifact_purges_its_stale_to_compose_row_from_another_day(
    fake_artifact_session: FakeArtifactSession,
) -> None:
    """Root-caused 2026-08-27.

    An artifact that goes unselected stays a normal pending candidate and
    can legitimately be re-picked on a LATER day -- but if its OLD day's
    to_compose row was never cleaned up (e.g. a dangling row from before
    this fix), re-selecting it must purge that old row, not leave two
    days' rows pointing at the same artifact_id (the exact aliasing that
    corrupted two real prod artifacts).
    """
    from algorand_shared.artifact_store import insert_artifact, revert_artifact_to_pending
    from algorand_shared.to_compose_selection import select_to_compose_for_day

    aid, _ = insert_artifact(service_id="svc-a", url=None, channel="brief", content="a")
    select_to_compose_for_day("2026-08-25")
    assert ("2026-08-25", 0) in fake_artifact_session.to_compose

    # Simulate the pre-fix world: the artifact goes back to pending WITHOUT
    # its old to_compose row being cleaned up.
    revert_artifact_to_pending(aid)
    assert ("2026-08-25", 0) in fake_artifact_session.to_compose  # still dangling

    select_to_compose_for_day("2026-08-28")

    assert ("2026-08-25", 0) not in fake_artifact_session.to_compose
    assert ("2026-08-28", 0) in fake_artifact_session.to_compose
    assert str(fake_artifact_session.to_compose[("2026-08-28", 0)]["artifact_id"]) == aid


@pytest.mark.usefixtures("fake_artifact_session")
def test_reclaim_never_reverts_an_artifact_that_is_also_currently_selected_elsewhere(
    fake_artifact_session: FakeArtifactSession,
) -> None:
    """Root-caused 2026-08-27 (two real prod artifacts corrupted this way).

    A stale to_compose row from a PAST day pointing at an artifact that is
    ALSO, separately, legitimately SELECTED for a CURRENT/future day must
    not have its status reverted -- only the stale row itself is garbage.
    Constructs the aliasing directly (bypassing the insert-time purge fix)
    to test reclaim's own defense-in-depth guard in isolation, in case a
    dangling row predates that fix.
    """
    from algorand_shared.artifact_store import SELECTED, insert_artifact
    from algorand_shared.to_compose_selection import (
        find_stale_selected_artifacts,
        reclaim_stale_selected_artifacts,
        select_to_compose_for_day,
    )

    aid, _ = insert_artifact(service_id="svc-a", url=None, channel="brief", content="a")
    select_to_compose_for_day("2026-08-28")
    assert fake_artifact_session.artifacts[aid]["status"] == SELECTED

    # Inject a stale PAST-day row aliased to the SAME artifact_id directly --
    # what a leftover pre-fix dangling row looks like.
    fake_artifact_session.to_compose[("2026-08-25", 0)] = {
        "compose_day": "2026-08-25",
        "slot": 0,
        "artifact_id": aid,
        "lane": "platform",
        "service_id": "svc-a",
        "picked_at": None,
    }

    stale = find_stale_selected_artifacts(today="2026-08-26")
    assert len(stale) == 1
    assert stale[0]["has_current_reference"] is True

    result = reclaim_stale_selected_artifacts(today="2026-08-26", dry_run=False)

    assert result["reclaimed"][0]["action"] == "stale_row_deleted_only"
    assert ("2026-08-25", 0) not in fake_artifact_session.to_compose
    # The CURRENT selection is completely untouched.
    assert fake_artifact_session.artifacts[aid]["status"] == SELECTED
    assert ("2026-08-28", 0) in fake_artifact_session.to_compose
