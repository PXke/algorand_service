"""x402 uptime/reachability check -- pay per call to learn whether a URL/IP is down from our servers.

See docs/x402-uptime-check-design.md for the full design rationale. PROTOTYPE,
not a live product: registered only when `settings.x402_uptime_enabled` is
True (default False) -- the pricing and both rate-limit numbers in
config.py's "x402 uptime check" block are reasoned defaults, not
owner-confirmed operating parameters (see the design doc's Open Questions).

One route: POST /api/v1/x402/uptime/check. A single GET, no body ever
downloaded (status line + headers only), SSRF-guarded on every hop the same
way media/x402_scan already are, cached (asymmetric TTL: an "up" result is
trusted longer than a "down" one), and rate-limited on two independent
dimensions -- per caller IP (abuse of us) and per target host (abuse of a
third party through us, the actual DDoS defense: bounded regardless of how
many different callers/wallets are involved).
"""
