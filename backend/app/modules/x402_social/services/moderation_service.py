"""Phase S2: community moderation -- report -> vote -> exponential ban (design doc section 5, owner sign-off 2026-09-03), plus the shared hard-delete machinery the section 8.1 admin lever also calls into (design doc sections 5.4.2/8.1). Ships behind settings.x402_social_moderation_enabled -- see api/routes.py.

Two identity/architecture notes, both load-bearing:

1. **Pre-gate free refusals need a KNOWN wallet, which a paid route does not
   have until settlement (section 4.1: "the payment IS the proof").** This
   module's own domain.not_registered_error already documents the same
   tension for registration ("that is architecturally impossible here...
   so this is a POST-gate, settled-then-refused check instead") and this
   module follows that SAME resolution for every check that only a
   PAYMENT can identify (the open-report concurrency cap, ban enforcement
   on a report/vote once the real payer is known). The one place this
   module diverges from that precedent is the report-COOLDOWN pre-gate
   the design doc explicitly calls out as a free 403 (section 5.3/5.4.1,
   "a platform-imposed throttle the caller could not have avoided by
   paying more"): api/routes.py resolves an OPTIONAL bearer session token
   (the SAME mechanism this module already uses for every free-
   authenticated route) to a wallet for that one free pre-check, purely as
   a convenience for a well-behaved caller -- the authoritative check
   still runs again here, post-gate, against the REAL settled payer, and
   refuses caller-fault (payment kept, 409) if a caller skipped or lied
   about the session. Money is never actually placed at risk either way;
   only the "was the refusal free" outcome differs by whether the caller
   identified itself in advance. This judgment call is called out in the
   shipping report; it is not implied verbatim by the design doc's text.

2. **The section 5.3 "resolver slot" LWT IS the case row's own conditional
   UPDATE** (`IF state = 'open'`, statements.X402SocialStmts.
   UPDATE_CASE_RESOLUTION) -- not a separate table. The design doc's own
   section 5.4.2 numbered order (audit record, then hard-delete content,
   then mark the case resolved) is followed for the AUDIT RECORD (written
   before any content is actually deleted, the ordering the design doc
   itself emphasizes: "the record that something was removed... must
   survive even though the content does not"), but the CASE ROW's own
   state/resolution_note/content_snapshot are all set in ONE atomic
   conditional UPDATE that ALSO serves as the exactly-once resolver claim,
   which necessarily runs BEFORE the hard-delete side effects it gates --
   deferring that claim would reopen exactly the double-resolution race
   the LWT exists to prevent. content_snapshot is decided in that same
   UPDATE (computed from the case's own already-known category + the
   just-read vote tally, so no second write is needed for it). The
   remaining, accepted risk: a process crash strictly between that UPDATE
   committing and the hard-delete finishing leaves the case durably marked
   'upheld' with its content_snapshot already scrubbed while some
   projection rows of the target still exist -- `_resolve_if_due` will not
   revisit an already-resolved case to retry, so this is a narrow,
   documented gap, not a silent one. Flagged in the shipping report.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime

from app.core.config import settings
from app.modules.x402_social.models.domain import (
    CASE_STATE_OPEN,
    CASE_STATE_REJECTED,
    CASE_STATE_UPHELD,
    CASE_VERDICTS,
    CASE_VOTE_SCAN_LIMIT,
    CATEGORY_ILLEGAL_CONTENT,
    GROUP_HARD_DELETE_MAX_PAGES,
    GROUP_HARD_DELETE_PAGE_SIZE,
    GROUP_MEMBER_SCAN_LIMIT,
    HARD_DELETE_SNAPSHOT_PLACEHOLDER,
    MAX_REPORT_NOTE_LEN,
    REMOVED_BY_ADMIN_LEVER,
    REMOVED_BY_COMMUNITY_VOTE,
    REPORT_CATEGORIES,
    TARGET_AGENT,
    TARGET_GROUP,
    TARGET_POST,
    TARGET_TYPES,
    VERDICT_REJECT,
    VERDICT_UPHOLD,
    CaseTally,
    RemovalRecord,
    SocialError,
    StoredCase,
    StoredStanding,
    compute_ban_seconds,
    compute_report_cooldown_seconds,
    offenses_in_decay_window,
)
from app.modules.x402_social.services.group_service import GroupService
from app.modules.x402_social.services.post_service import PostService, _new_post_or_comment_id
from app.modules.x402_social.stores.base import SocialStore
from app.modules.x402_social.stores.factory import get_social_store

logger = logging.getLogger(__name__)

# wallet -> that wallet's profile creation epoch, or None if unregistered.
# Bound in api/routes.py to a thin wrapper over profile_service.get(...) --
# same decoupling precedent as PostService.IsRegisteredLookup: this module
# never imports profile_service directly.
RegisteredSinceLookup = Callable[[str], int | None]

# The escalating report-cooldown formula doubles per consecutive rejection
# (design doc section 5.4.1: "base 15 min doubling per consecutive
# rejection") -- unlike the ban formula's multiplier, this is not exposed
# as its own setting (the config sketch in the design doc's section 1 lists
# only a base and a cap for the cooldown), so it is a module constant here.
_REPORT_COOLDOWN_MULTIPLIER = 2


class ModerationService:
    """Phase S2's case lifecycle (open/vote/resolve), standing/karma bookkeeping, and the shared hard-delete mechanics the section 8.1 admin lever also uses."""

    def __init__(
        self,
        store: SocialStore | None = None,
        *,
        post_service: PostService | None = None,
        group_service: GroupService | None = None,
        registered_since: RegisteredSinceLookup | None = None,
    ) -> None:
        """Take explicit collaborators for tests; otherwise resolve the configured store lazily.

        `registered_since` has no lazy default: without one, `cast_vote`
        always refuses eligibility (fail closed) -- same contract as
        PostService's own `is_registered` seam.
        """
        self._store = store
        self._post_service = post_service
        self._group_service = group_service
        self._registered_since = registered_since

    @property
    def store(self) -> SocialStore:
        """The injected store, or the process-wide one built from settings."""
        return self._store or get_social_store()

    @property
    def post_service(self) -> PostService:
        """The injected PostService, or a fresh one bound to this same store."""
        return self._post_service or PostService(self._store)

    @property
    def group_service(self) -> GroupService:
        """The injected GroupService, or a fresh one bound to this same store."""
        return self._group_service or GroupService(self._store)

    # ------------------------------------------------------------- #
    # Standing (design doc section 5.2/5.4/5.4.1) -- full karma read, plus
    # the two pre/post-gate throttle checks routes.py calls into.
    # ------------------------------------------------------------- #
    def _get_standing_or_default(self, wallet: str) -> StoredStanding:
        return self.store.get_standing(wallet) or StoredStanding(wallet=wallet)

    def standing(self, wallet: str) -> StoredStanding:
        """Public read (GET /agents/{wallet}/standing) -- a wallet with no row yet reads as all-zero, never a 404 (design doc section 5.2: public, "so counterparties can check who they're dealing with" -- zero history is itself information)."""
        return self._get_standing_or_default(wallet)

    def report_cooldown_until(self, wallet: str) -> int:
        """Epoch seconds `wallet` must wait until before filing another report, 0 if none is active (design doc section 5.4.1)."""
        standing = self._get_standing_or_default(wallet)
        now = int(datetime.now(tz=UTC).timestamp())
        return (
            standing.report_cooldown_until_epoch
            if standing.report_cooldown_until_epoch > now
            else 0
        )

    def banned_until(self, wallet: str) -> int:
        """Epoch seconds `wallet` is banned until, 0 if not currently banned (design doc section 5.4)."""
        standing = self._get_standing_or_default(wallet)
        now = int(datetime.now(tz=UTC).timestamp())
        return standing.banned_until_epoch if standing.banned_until_epoch > now else 0

    # ------------------------------------------------------------- #
    # Target resolution -- shared by open_report and admin_remove.
    # ------------------------------------------------------------- #
    def _resolve_target(self, target_type: str, target_id: str) -> tuple[str, str] | None:
        """(content_snapshot, target_wallet) for a live target, or None if it does not exist / is already gone."""
        if target_type == TARGET_POST:
            post = self.post_service.get(target_id)
            if post is None or post.deleted or post.hidden_platform:
                return None
            return post.body_md, post.author
        if target_type == TARGET_AGENT:
            # target_id IS the wallet -- there is no separate agent id
            # anywhere in this module (domain.AgentProfile's own docstring).
            created_at = self._registered_since(target_id) if self._registered_since else None
            if created_at is None:
                return None
            return f"agent wallet {target_id}", target_id
        if target_type == TARGET_GROUP:
            group = self.group_service.get(target_id)
            if group is None or group.hidden_platform:
                return None
            return f'group "{group.name}": {group.description}', group.owner
        return None

    # ------------------------------------------------------------- #
    # Open a report (design doc section 5.3 step 1)
    # ------------------------------------------------------------- #
    def open_report(
        self,
        *,
        reporter: str,
        target_type: str,
        target_id: str,
        category: str,
        note: str,
        settlement_tx_id: str,
        now: datetime | None = None,
    ) -> StoredCase:
        """The paid product write for POST /reports.

        Guard order (design doc section 5.3 step 1): the COOLDOWN pre-gate
        lives in the route (free 403, before the payment gate) -- this
        method re-checks it here too, authoritatively, against the REAL
        settled `reporter` (caller-fault 409 if still on cooldown -- the
        module docstring's note 1 explains why this cannot ALSO be free at
        this point: payment has already settled by the time this runs).
        Then the open-report CONCURRENCY CAP (an LWT slot claim,
        caller-fault 409 if already at `x402_social_report_max_open`).
        Then the one-open-case-per-target claim (caller-fault 409 pointing
        at the existing case_id if the target already has an open case --
        "the reporter should have voted instead"); losing that claim
        releases the concurrency-cap slot just claimed, since this report
        never legitimately opened a new case.

        Raises SocialError("wallet_banned", ..., 403) if `reporter` is
        currently banned (design doc section 5.4: a banned agent cannot
        report).
        Raises SocialError("invalid_request", ..., 400) for an unrecognized
        target_type/category (defense in depth -- the route's msgspec
        Literal fields already reject this before decode).
        Raises SocialError("not_found", ..., 404) if the target does not
        exist or is already gone.
        """
        if self.banned_until(reporter) > 0:
            raise SocialError(
                "wallet_banned",
                "A banned wallet cannot file reports. Payment has settled but no case was opened.",
                http_status=403,
            )
        if self.report_cooldown_until(reporter) > 0:
            raise SocialError(
                "report_cooldown_active",
                "This wallet is under a report-filing cooldown after a recent rejected "
                "report. Payment has settled but no case was opened.",
                http_status=409,
            )
        if target_type not in TARGET_TYPES:
            raise SocialError(
                "invalid_request", "target_type is not a recognized type", http_status=400
            )
        if category not in REPORT_CATEGORIES:
            raise SocialError(
                "invalid_request", "category is not a recognized category", http_status=400
            )
        resolved = self._resolve_target(target_type, target_id)
        if resolved is None:
            raise SocialError("not_found", "No target with that id", http_status=404)
        content_snapshot, target_wallet = resolved

        moment = now or datetime.now(tz=UTC)
        epoch = int(moment.timestamp())
        case_id = _new_post_or_comment_id()
        max_open = settings.x402_social_report_max_open

        if not self.store.try_claim_reporter_slot(
            reporter=reporter, case_id=case_id, max_open=max_open
        ):
            raise SocialError(
                "too_many_open_reports",
                f"This wallet already has {max_open} open reports. Payment has settled but no "
                "new case was opened -- wait for one of your existing reports to resolve, "
                "then retry.",
                http_status=409,
            )

        if not self.store.try_claim_open_case_for_target(target_id=target_id, case_id=case_id):
            self.store.release_reporter_slot(reporter=reporter, case_id=case_id)
            existing = self.store.get_open_case_id_for_target(target_id)
            raise SocialError(
                "case_already_open",
                "This target already has an open case. Payment has settled but no new case "
                f"was opened -- vote on the existing case instead (case_id={existing or 'unknown'}).",
                http_status=409,
            )

        case = StoredCase(
            case_id=case_id,
            target_type=target_type,
            target_id=target_id,
            target_wallet=target_wallet,
            category=category,
            note=note[:MAX_REPORT_NOTE_LEN],
            reporter=reporter,
            settlement_tx_id=settlement_tx_id,
            content_snapshot=content_snapshot,
            opened_at_epoch=epoch,
            window_ends_at_epoch=epoch + settings.x402_social_case_window_seconds,
            state=CASE_STATE_OPEN,
        )
        # Both claims above have no TTL and are only ever released as part of
        # a case's own resolution (_resolve_case, below) -- which requires a
        # case row that, at this point, does not exist yet. If either write
        # below throws, this case never becomes durable and can therefore
        # never resolve, so without this compensating release BOTH claims
        # would leak forever: the target becomes permanently unreportable
        # and the reporter permanently loses one of their max_open open-
        # report slots (finding-class 2026-09-03, A1). Same "wrap the
        # post-claim writes, release on any failure before re-raising"
        # pattern group_service.create already uses for its own claim-then-
        # write sequence (the name claim there) -- including that same
        # method's accepted edge case: if insert_case itself succeeds but
        # only _bump_reported_count fails, the claims are still released
        # even though a case now durably exists, exactly mirroring what
        # group_service.create already accepts for a group whose name claim
        # is freed after the group row itself was already stored. A full
        # compensating rollback of insert_case is overkill for that edge
        # case, same reasoning as this codebase's other "our failure, not
        # the caller's" recovery paths.
        try:
            self.store.insert_case(case)
            self._bump_reported_count(target_wallet)
        except Exception:
            self._release_open_report_claims(
                target_id=target_id, case_id=case_id, reporter=reporter
            )
            raise

        if category == CATEGORY_ILLEGAL_CONTENT:
            # No real operator-paging integration exists in this backend
            # (checked: no Slack/PagerDuty/alert module anywhere in
            # backend/app) -- an ERROR-level log line is the best available
            # mechanism until one is built; flagged in the shipping report.
            logger.error(
                "x402 social moderation: OPERATOR PAGE -- illegal_content report opened, "
                "case_id=%s target_type=%s target_id=%s reporter=%s. A 24h community vote is "
                "not an acceptable response time for this category (design doc section 5.5) "
                "-- the section 8.1 admin lever can pre-empt this vote.",
                case_id,
                target_type,
                target_id,
                reporter,
            )
        return case

    def _release_open_report_claims(self, *, target_id: str, case_id: str, reporter: str) -> None:
        """Best-effort compensating release of the two claims open_report takes before the case is durably stored (A1, 2026-09-03) -- see that method's own comment on why leaving either claimed forever is the bug. Never masks a release failure: logs a warning with the exact wallet/target that may now need manual cleanup, then returns (the caller re-raises the ORIGINAL exception)."""
        try:
            self.store.release_open_case_for_target(target_id=target_id, case_id=case_id)
        except Exception:
            logger.warning(
                "x402 social moderation: failed to release the open-case-per-target claim "
                "for target_id=%s case_id=%s after an open_report failure -- this target may "
                "now be permanently unreportable; manual cleanup may be required",
                target_id,
                case_id,
                exc_info=True,
            )
        try:
            self.store.release_reporter_slot(reporter=reporter, case_id=case_id)
        except Exception:
            logger.warning(
                "x402 social moderation: failed to release reporter=%s's open-report slot "
                "for case_id=%s after an open_report failure -- this reporter's open-report "
                "count may now be permanently overcounted by one",
                reporter,
                case_id,
                exc_info=True,
            )

    def _bump_reported_count(self, target_wallet: str) -> None:
        """Increment `target_wallet`'s reported_count by one -- an atomic read-modify-write via mutate_standing (fixed 2026-09-03, A2: this used to be a plain get_standing-then-upsert_standing pair, which raced under concurrent opens against the same wallet the same way every other standing mutation did -- see mutate_standing's own docstring)."""
        if not target_wallet:
            return

        def _mutate(standing: StoredStanding) -> StoredStanding:
            standing.reported_count += 1
            return standing

        self.store.mutate_standing(target_wallet, _mutate)

    # ------------------------------------------------------------- #
    # Read cases (design doc section 5.2) -- both touch _resolve_if_due.
    # ------------------------------------------------------------- #
    def get_case(self, case_id: str) -> StoredCase | None:
        """Return one case, lazily resolving it first if its window has elapsed."""
        case = self.store.get_case(case_id)
        if case is None:
            return None
        return self._resolve_if_due(case)

    def list_open_cases(self, *, limit: int) -> list[StoredCase]:
        """Return open cases newest-first, clamped to x402_social_max_results -- the free 'jury duty' feed. Each is lazily resolved if due before being returned."""
        clamped = max(1, min(limit, settings.x402_social_max_results))
        cases = self.store.list_open_cases(limit=clamped)
        return [self._resolve_if_due(c) for c in cases]

    def vote_tally(self, case_id: str) -> CaseTally:
        """A case's current uphold/reject totals -- the route only exposes this once the case is resolved (design doc section 5.6 Q7: hidden until then)."""
        return self.store.get_case_vote_totals(case_id)

    # ------------------------------------------------------------- #
    # Vote (design doc section 5.3 step 2)
    # ------------------------------------------------------------- #
    def cast_vote(
        self,
        *,
        case_id: str,
        voter: str,
        verdict: str,
        settlement_tx_id: str,
        now: datetime | None = None,
    ) -> StoredCase:
        """The paid product write for POST /cases/{case_id}/vote.

        Eligibility, checked in order: the case must exist and (after a
        lazy resolve-if-due touch) still be open; the voter must be neither
        the reported wallet nor the reporter; the voter's OWN registration
        must PREDATE this case's opening (design doc section 5.3: "a wallet
        minted after the fight started cannot vote in it... this plus the
        two payments is the entire sybil defense, so it is load-bearing");
        the voter must not currently be banned (design doc section 5.4: "a
        banned agent must not help swing the very system that banned it").
        One vote per wallet per case, forever, via LWT.

        Documented, not fully closed (A4, 2026-09-03): there is a TOCTOU
        race between this method's open-state check above (including the
        `_resolve_if_due` touch) and `try_add_case_vote`'s own LWT below --
        the case can lazily resolve, elsewhere, in that gap. A vote that
        wins its per-voter LWT after that point is still durably counted
        (`increment_case_vote_total` runs unconditionally once the LWT is
        won) even though the case that decided the verdict is already
        resolved: the payer's vote is real and paid-for, but it will never
        be reflected in that case's own tally or in `_settle_vote_karma`'s
        bookkeeping for this case, since resolution already read the vote
        list once and will not revisit an already-resolved case. No cheap
        fix closes this without a larger redesign: the case's own state and
        the per-voter vote LWT live in different tables, so there is no
        single conditional write that can guard both at once the way
        `UPDATE_CASE_RESOLUTION`'s own `IF state = 'open'` guards resolution
        itself. Reported as documented-not-fully-closed rather than
        silently unmentioned.
        """
        if verdict not in CASE_VERDICTS:
            raise SocialError(
                "invalid_request", "verdict must be 'uphold' or 'reject'", http_status=400
            )
        case = self.store.get_case(case_id)
        if case is None:
            raise SocialError("not_found", "No case with that id", http_status=404)
        case = self._resolve_if_due(case)
        if case.state != CASE_STATE_OPEN:
            raise SocialError(
                "case_already_resolved",
                f"This case is already resolved ({case.state}). Payment has settled but no "
                "vote was recorded.",
                http_status=409,
            )
        if voter == case.target_wallet or voter == case.reporter:
            raise SocialError(
                "not_eligible_to_vote",
                "The reported wallet and the reporter cannot vote on their own case. Payment "
                "has settled but no vote was recorded.",
                http_status=403,
            )
        registered_at = self._registered_since(voter) if self._registered_since else None
        if registered_at is None or registered_at >= case.opened_at_epoch:
            raise SocialError(
                "not_eligible_to_vote",
                "Only agents registered before this case opened may vote on it. Payment has "
                "settled but no vote was recorded.",
                http_status=403,
            )
        if self.banned_until(voter) > 0:
            raise SocialError(
                "wallet_banned",
                "A banned wallet cannot vote on a moderation case. Payment has settled but no "
                "vote was recorded.",
                http_status=403,
            )
        moment = now or datetime.now(tz=UTC)
        won = self.store.try_add_case_vote(
            case_id=case_id,
            voter=voter,
            verdict=verdict,
            settlement_tx_id=settlement_tx_id,
            voted_at_epoch=int(moment.timestamp()),
        )
        if not won:
            raise SocialError(
                "already_voted",
                "This wallet has already voted on this case. Payment has settled but no new "
                "vote was recorded -- one vote per wallet per case, forever.",
                http_status=409,
            )
        self.store.increment_case_vote_total(case_id, verdict=verdict)
        return case

    # ------------------------------------------------------------- #
    # Resolution -- lazy, no scheduler (design doc section 5.3 step 3).
    # See the module docstring's note 2 for the exact ordering this
    # implements versus the design doc's literal numbered list.
    # ------------------------------------------------------------- #
    def _resolve_if_due(self, case: StoredCase, *, now: datetime | None = None) -> StoredCase:
        """Resolve `case` if its window has elapsed and it is still open. Idempotent to observe: a case already resolved, or not yet due, is returned unchanged."""
        if case.state != CASE_STATE_OPEN:
            return case
        moment = now or datetime.now(tz=UTC)
        if int(moment.timestamp()) < case.window_ends_at_epoch:
            return case
        return self._resolve_case(case, now=moment)

    def _resolve_case(self, case: StoredCase, *, now: datetime) -> StoredCase:
        tally = self.store.get_case_vote_totals(case.case_id)
        total = tally.uphold + tally.reject
        quorum = settings.x402_social_case_quorum
        # Exact-fraction comparison (fixed 2026-09-03, A5) -- NOT
        # `(tally.uphold / total) >= ratio` as a float threshold: with the
        # numerator/denominator pinned to 2/3, that float form could never
        # land on an exact two-thirds split (4/6, 6/9, 8/12 all computed as
        # 0.6666... < the float literal 0.667 the OLD single-float setting
        # used, and resolved REJECTED even though "at least two-thirds" was
        # the evident intent). `tally.uphold * ratio_den >= ratio_num * total`
        # is exactly equivalent to "uphold ratio >= ratio_num/ratio_den" with
        # pure integer arithmetic -- no floating-point boundary, and an exact
        # split always resolves upheld regardless of what these two ints are
        # set to. x402_social_case_uphold_ratio_numerator/_denominator
        # replace the old single float setting (config.py) for exactly this
        # reason.
        ratio_num = settings.x402_social_case_uphold_ratio_numerator
        ratio_den = settings.x402_social_case_uphold_ratio_denominator
        upheld = (
            total >= quorum and tally.uphold * ratio_den >= ratio_num * total if total else False
        )
        ratio_display = ratio_num / ratio_den if ratio_den else 0.0
        resolved_epoch = int(now.timestamp())

        if upheld:
            state = CASE_STATE_UPHELD
            note = (
                f"upheld: {tally.uphold} of {total} votes reached quorum "
                f"(>= {quorum}) and the required uphold ratio (>= {ratio_display:.0%})"
            )
        else:
            state = CASE_STATE_REJECTED
            if total < quorum:
                note = (
                    f"report did not reach quorum: {total} of {quorum} required votes; "
                    "resolved as not-upheld"
                )
            else:
                note = (
                    f"report did not reach the required uphold ratio: {tally.uphold} of "
                    f"{total} votes ({tally.uphold / total:.0%} < {ratio_display:.0%}); "
                    "resolved as not-upheld"
                )

        content_snapshot = case.content_snapshot
        hard_delete_bound = upheld and case.category == CATEGORY_ILLEGAL_CONTENT
        if hard_delete_bound:
            content_snapshot = HARD_DELETE_SNAPSHOT_PLACEHOLDER

        resolved = replace(
            case,
            state=state,
            resolved_at_epoch=resolved_epoch,
            resolution_note=note,
            content_snapshot=content_snapshot,
        )
        won = self.store.resolve_case(resolved)
        if not won:
            # Another process already resolved this case -- idempotent to
            # observe, no consequences applied here.
            return self.store.get_case(case.case_id) or resolved

        self.store.release_reporter_slot(reporter=case.reporter, case_id=case.case_id)

        if upheld:
            self._apply_upheld_consequences(
                resolved, tally=tally, hard_delete_bound=hard_delete_bound
            )
            self._reset_reporter_streak(case.reporter)
        else:
            self._escalate_reporter_cooldown(case.reporter, resolved_at_epoch=resolved_epoch)

        self._settle_vote_karma(case.case_id, upheld=upheld, limit=CASE_VOTE_SCAN_LIMIT)
        return resolved

    def _apply_upheld_consequences(
        self, case: StoredCase, *, tally: CaseTally, hard_delete_bound: bool
    ) -> None:
        """Design doc section 5.3 step 4: category-dependent content action, plus a ban on the target wallet always -- "target agent (or post-author, cascading) => a ban", read as: every upheld case bans whichever wallet the target resolves to (the agent itself, a post's author, or a group's owner)."""
        if hard_delete_bound:
            self._hard_delete_upheld(case, tally=tally)
        elif case.target_type == TARGET_POST:
            post = self.post_service.get(case.target_id)
            if post is not None and not post.deleted and not post.hidden_platform:
                self.store.mark_post_hidden_platform(post)
        elif case.target_type == TARGET_GROUP:
            group = self.group_service.get(case.target_id)
            if group is not None and not group.hidden_platform:
                self.store.mark_group_hidden_platform(group)
        # target_type == TARGET_AGENT: no content to act on, ban only.

        self._apply_ban(case.target_wallet, resolved_at_epoch=case.resolved_at_epoch)

    def _apply_ban(self, wallet: str, *, resolved_at_epoch: int) -> None:
        """The section 5.4 ban formula, applied once per upheld case against `wallet`, via mutate_standing (fixed 2026-09-03, A2 -- see that method's own docstring: this is the exact mutation a concurrent stale-read overwrite used to silently drop)."""
        if not wallet:
            return

        def _mutate(standing: StoredStanding) -> StoredStanding:
            offenses_in_window = offenses_in_decay_window(
                standing.offenses,
                now_epoch=resolved_at_epoch,
                decay_days=settings.x402_social_offense_decay_days,
            )
            ban_seconds = compute_ban_seconds(
                offenses_in_window,
                base_seconds=settings.x402_social_ban_base_seconds,
                multiplier=settings.x402_social_ban_multiplier,
                cap_seconds=settings.x402_social_ban_cap_seconds,
            )
            standing.offense_count += 1
            standing.last_offense_at_epoch = resolved_at_epoch
            standing.offenses = [*standing.offenses, resolved_at_epoch]
            standing.banned_until_epoch = resolved_at_epoch + ban_seconds
            return standing

        self.store.mutate_standing(wallet, _mutate)

    def _reset_reporter_streak(self, reporter: str) -> None:
        """An upheld report resets the reporter's report_rejection_streak to 0 (design doc section 5.4.1, chosen explicitly over merely not-incrementing -- see that section's own argument). Via mutate_standing (A2)."""
        if not reporter:
            return

        def _mutate(standing: StoredStanding) -> StoredStanding:
            standing.report_rejection_streak = 0
            return standing

        self.store.mutate_standing(reporter, _mutate)

    def _escalate_reporter_cooldown(self, reporter: str, *, resolved_at_epoch: int) -> None:
        """Every rejected resolution increments the reporter's rejection streak and sets a filing cooldown that doubles per consecutive rejection, capped (design doc section 5.4.1). Does NOT shorten an already-running cooldown -- only the streak resets on an upheld report; a rejection's own cooldown always runs its full course. Via mutate_standing (A2)."""
        if not reporter:
            return

        def _mutate(standing: StoredStanding) -> StoredStanding:
            standing.rejected_report_count += 1
            standing.report_rejection_streak += 1
            cooldown_seconds = compute_report_cooldown_seconds(
                standing.report_rejection_streak,
                base_seconds=settings.x402_social_report_cooldown_base_seconds,
                multiplier=_REPORT_COOLDOWN_MULTIPLIER,
                cap_seconds=settings.x402_social_report_cooldown_cap_seconds,
            )
            standing.report_cooldown_until_epoch = resolved_at_epoch + cooldown_seconds
            return standing

        self.store.mutate_standing(reporter, _mutate)

    def _settle_vote_karma(self, case_id: str, *, upheld: bool, limit: int) -> None:
        """Every voter's votes_cast increments; votes_matched_resolution increments too iff their vote matched the outcome (design doc section 5.3's own resolver bookkeeping note). Each voter's row is mutated via mutate_standing (A2) -- a separate atomic RMW per voter, since each touches a different wallet."""
        resolution_verdict = VERDICT_UPHOLD if upheld else VERDICT_REJECT
        for voter, verdict in self.store.list_case_votes(case_id, limit=limit):
            matched = verdict == resolution_verdict

            def _mutate(standing: StoredStanding, matched: bool = matched) -> StoredStanding:
                standing.votes_cast += 1
                if matched:
                    standing.votes_matched_resolution += 1
                return standing

            self.store.mutate_standing(voter, _mutate)

    # ------------------------------------------------------------- #
    # Hard-delete machinery (design doc section 5.4.2) -- shared by the
    # community resolver above AND the section 8.1 admin lever below, so
    # neither path duplicates the actual deletion mechanics.
    # ------------------------------------------------------------- #
    def _hard_delete_upheld(self, case: StoredCase, *, tally: CaseTally) -> None:
        """Resolution order: audit record FIRST, then hard-delete the actual content (design doc section 5.4.2 -- "the record that something was removed, and why, must survive even though the content does not"). The case row's own state/content_snapshot were already flipped atomically by the resolver-slot claim in _resolve_case -- see the module docstring's note 2."""
        removal = RemovalRecord(
            case_id=case.case_id,
            target_type=case.target_type,
            target_id=case.target_id,
            target_wallet=case.target_wallet,
            category=case.category,
            removed_by=REMOVED_BY_COMMUNITY_VOTE,
            resolved_at_epoch=case.resolved_at_epoch,
            uphold_votes=tally.uphold,
            reject_votes=tally.reject,
        )
        self.store.insert_removal(removal)
        self._hard_delete_content(target_type=case.target_type, target_id=case.target_id)

    def _hard_delete_content(self, *, target_type: str, target_id: str) -> None:
        """The actual removal mechanics, shared by the community resolver (above) and admin_remove (below). Idempotent: safe to call on an already (partially) deleted target."""
        if target_type == TARGET_POST:
            post = self.post_service.get(target_id)
            if post is not None:
                self.store.hard_delete_post(post)
        elif target_type == TARGET_GROUP:
            self._hard_delete_group(target_id)
        # TARGET_AGENT has no content of its own to hard-delete -- only a ban.

    def _hard_delete_group(self, group_id: str) -> None:
        """Hard-delete a group and every one of its posts (design doc section 5.4.2 -- the owner's incitement-to-genocide example: full removal, not discovery-hiding).

        Walks the group's own feed in bounded pages (GROUP_HARD_DELETE_PAGE_SIZE
        each), hard-deleting every post found, up to GROUP_HARD_DELETE_MAX_PAGES
        pages in this one touch -- see those constants' own docstrings for
        the accepted single-touch-completion trade at this competition's
        realistic scale (a group with more posts than that cap would need a
        follow-up manual nudge; a documented limit, not a silent one).
        Reads the store directly (not post_service.list_group_feed, which
        clamps to x402_social_max_results) so the page size is not
        constrained by that unrelated read-page setting.
        """
        group = self.group_service.get(group_id)
        if group is None:
            return  # already gone -- e.g. a retry after a prior partial completion
        name_norm = group.name.strip().lower()

        for _ in range(GROUP_HARD_DELETE_MAX_PAGES):
            page = self.store.list_group_feed(group_id, limit=GROUP_HARD_DELETE_PAGE_SIZE)
            if not page:
                break
            for post in page:
                self.store.hard_delete_post(post)
            if len(page) < GROUP_HARD_DELETE_PAGE_SIZE:
                break

        for wallet in self.store.list_group_member_wallets(group_id, limit=GROUP_MEMBER_SCAN_LIMIT):
            self.store.delete_membership(group_id, wallet)
        self.store.delete_group_memberships_partition(group_id)

        self.store.hard_delete_group_shell(group, name_norm=name_norm)

    # ------------------------------------------------------------- #
    # Section 8.1 admin emergency lever -- immediate, no vote, payment
    # status irrelevant. Shares the exact hard-delete mechanics above.
    # ------------------------------------------------------------- #
    def admin_remove(
        self,
        *,
        target_type: str,
        target_id: str,
        category: str,
        hard_delete: bool,
        now: datetime | None = None,
    ) -> RemovalRecord | None:
        """The section 8.1 lever: `require_admin_wallet`-gated by the ROUTE (backend/app/modules/admin/api/routes.py), never here.

        `hard_delete=True` is only ever legitimate within the
        illegal_content scope (design doc section 5.4.2: "there is no
        admin or voter discretion to hard-delete under any other
        category") -- the ROUTE is responsible for refusing
        `hard_delete=True` outside that category before ever calling this;
        this method does exactly what it is told, the same "the route owns
        the policy check, the service owns the mechanism" split every
        other service in this module already makes.

        Raises SocialError("not_found", ..., 404) if the target does not
        exist. Returns None when `hard_delete` is False (a plain
        tombstone/hide has no audit record of its own -- x402_social_removals
        is specifically the hard-delete audit table, design doc section
        5.4.2's own schema) -- callers wanting a record of a soft hide use
        their own logs, same as this module's log line on illegal_content
        report opening.
        """
        resolved = self._resolve_target(target_type, target_id)
        if resolved is None:
            raise SocialError("not_found", "No target with that id", http_status=404)
        _content_snapshot, target_wallet = resolved
        moment = now or datetime.now(tz=UTC)
        resolved_epoch = int(moment.timestamp())

        if not hard_delete:
            if target_type == TARGET_POST:
                post = self.post_service.get(target_id)
                if post is not None and not post.hidden_platform:
                    self.store.mark_post_hidden_platform(post)
            elif target_type == TARGET_GROUP:
                group = self.group_service.get(target_id)
                if group is not None and not group.hidden_platform:
                    self.store.mark_group_hidden_platform(group)
            return None

        removal = RemovalRecord(
            case_id=_new_post_or_comment_id(),
            target_type=target_type,
            target_id=target_id,
            target_wallet=target_wallet,
            category=category,
            removed_by=REMOVED_BY_ADMIN_LEVER,
            resolved_at_epoch=resolved_epoch,
            uphold_votes=0,
            reject_votes=0,
        )
        self.store.insert_removal(removal)
        self._hard_delete_content(target_type=target_type, target_id=target_id)
        return removal
