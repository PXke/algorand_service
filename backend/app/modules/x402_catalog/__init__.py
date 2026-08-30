"""x402 catalog: one free route describing every x402 product route that is live.

`GET /api/v1/x402` returns a JSON document an agent can read before paying
anything: each route's method, path, whether it is paid, its price, the
assets accepted, the network, the payTo address, and an input example where
the route declares one. The catalog is built from a static roster in
services/catalog.py, and each product is included only under the SAME
conditions falcon_main.py registers it (x402_enabled plus that product's
store setting != "memory"), so it never advertises a route that 404s.
"""
