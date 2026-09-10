"""Algorand Open Registry periodic liveness re-check + admin blurb-draft helper (roadmap item 26).

Off by default (ECOSYSTEM_PROBE_ENABLED): it sends real, unpaid,
SSRF-guarded requests to third-party sites, so it is enabled deliberately
per deployment, not by shipping code (see workers/app/core/config.py).
Never touches a rejected entry: this beat only
re-checks pending/approved rows, matching the admin queue's own review
scope.
"""
