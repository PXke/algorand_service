"""Algorand Open Registry periodic liveness re-check + admin blurb-draft helper (roadmap item 26).

Off by default (ECOSYSTEM_PROBE_ENABLED) -- mirrors x402_probe's own
"real, unpaid, SSRF-guarded requests are enabled deliberately per
deployment, not by shipping code" convention (see
workers/app/core/config.py). Never touches a rejected entry: this beat only
re-checks pending/approved rows, matching the admin queue's own review
scope.
"""
