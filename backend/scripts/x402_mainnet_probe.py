#!/usr/bin/env python3
"""Mainnet probe payment: one labelled, real USDC payment through /api/v1/x402/list.

This is the CLAUDE.md section 9 "probe" carve-out: a single self-verification
payment, labelled PROBE in the listing itself, excluded from any volume claim.
It proves the live mainnet settle path (facilitator settle, feePayer leg,
prod ledger write, nginx header fix under a real payment header) end-to-end,
which nothing else has done yet.

Usage:
  python3 x402_mainnet_probe.py gen            # generate a disposable payer wallet
  python3 x402_mainnet_probe.py status         # show payer ALGO/USDC balances + opt-in
  python3 x402_mainnet_probe.py optin          # opt the payer into USDC (needs ~0.2 ALGO)
  python3 x402_mainnet_probe.py pay            # run the real probe payment + verify
  python3 x402_mainnet_probe.py pay-all        # one PROBE payment per live paid route + verify each
  python3 x402_mainnet_probe.py pay-all --dry-run   # unpaid 402s only: decode + print each offer

`pay-all` covers, in order: POST /x402/board, POST /x402/grades,
GET /x402/grades/score, POST /x402/features/<id>/vote (on a request it first
files for free) and GET /x402/features/demand. Every step is labelled PROBE,
verified independently against the mainnet indexer, and continues past a
failure so the summary table at the end shows every route's state. A step
aborts before paying if the client selector picks anything but USDC.

The payer mnemonic lives in ./probe_wallet.json next to this script. It is a
DISPOSABLE wallet meant to hold ~$1 of USDC and ~0.5 ALGO -- never the payTo.
Run with the backend venv: /opt/g/algorand/backend/.venv/bin/python3
"""

from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

import algosdk
import msgpack
import requests
from algosdk import account, mnemonic, transaction
from algosdk.v2client import algod, indexer

if TYPE_CHECKING:
    from x402.http.x402_http_client import x402HTTPClientSync

API = "https://algorand-api.pxke.me"
ALGOD_URL = "https://mainnet-api.algonode.cloud"
INDEXER_URL = "https://mainnet-idx.algonode.cloud"
MAINNET_CAIP2 = "algorand:wGHE2Pwdvd7S12BL5FaOP20EGYesN73ktiC1qzkkit8="
USDC_ASA = 31566704
PAY_TO = "KSAVOYTVNB7A6NKCM4W2WBOOGFHWH2SEGR5T6OGB7THCAT5E36LDFEBTII"
WALLET_FILE = Path(__file__).with_name("probe_wallet.json")

SEARCH_URL = f"{API}/api/v1/x402/search"

PROBE_LISTING = {
    "url": SEARCH_URL,
    "price": "$0.00",
    "description": (
        "PROBE: marketplace self-verification listing. One labelled mainnet payment "
        "to prove the settle path end-to-end; excluded from any volume claim per "
        "the challenge rules' probe carve-out. Safe to delist."
    ),
    "assets": ["USDC"],
    "tags": ["probe", "self-test"],
}


class WorkingAvmSigner:
    """Client signer that works against x402-avm==2.0.2 (see docs/x402-facilitator.md)."""

    def __init__(self, mnemonic_phrase: str) -> None:
        """Derive the signing key and address from a 25-word mnemonic."""
        self._secret_key_b64 = mnemonic.to_private_key(mnemonic_phrase)
        raw = base64.b64decode(self._secret_key_b64)
        self._address = algosdk.encoding.encode_address(raw[32:])

    @property
    def address(self) -> str:
        """The payer address this signer signs for."""
        return self._address

    def sign_transactions(
        self, unsigned_txns: list[bytes], indexes_to_sign: list[int]
    ) -> list[bytes | None]:
        """Sign the msgpack-encoded txns at `indexes_to_sign`; None for the rest."""
        result: list[bytes | None] = []
        for i, txn_bytes in enumerate(unsigned_txns):
            if i in indexes_to_sign:
                txn_dict = msgpack.unpackb(txn_bytes, raw=False)
                txn = algosdk.encoding.msgpack_decode(txn_dict)
                signed = txn.sign(self._secret_key_b64)
                result.append(base64.b64decode(algosdk.encoding.msgpack_encode(signed)))
            else:
                result.append(None)
        return result


def load_wallet() -> tuple[str, str]:
    """(address, mnemonic) from the disposable wallet file, or exit."""
    if not WALLET_FILE.exists():
        sys.exit(f"no {WALLET_FILE}; run `gen` first")
    data = json.loads(WALLET_FILE.read_text())
    return data["address"], data["mnemonic"]


def cmd_gen() -> None:
    """Generate the disposable payer wallet file (refuses to overwrite)."""
    if WALLET_FILE.exists():
        sys.exit(f"{WALLET_FILE} already exists; refusing to overwrite")
    sk, addr = account.generate_account()
    WALLET_FILE.write_text(
        json.dumps({"address": addr, "mnemonic": mnemonic.from_private_key(sk)}, indent=2)
    )
    WALLET_FILE.chmod(0o600)
    print("Disposable probe payer wallet generated.")
    print("Address:", addr)
    print(
        "Fund it with ~0.5 ALGO and ~1 USDC (asset 31566704), then run `optin` if needed, then `pay`."
    )


def _client() -> algod.AlgodClient:
    return algod.AlgodClient("", ALGOD_URL)


def cmd_status() -> None:
    """Print the payer's ALGO/USDC balances and USDC opt-in state."""
    addr, _ = load_wallet()
    info = _client().account_info(addr)
    algo = info.get("amount", 0) / 1e6
    usdc = next(
        (a["amount"] / 1e6 for a in info.get("assets", []) if a["asset-id"] == USDC_ASA), None
    )
    print("Address:", addr)
    print(f"ALGO: {algo}")
    print("USDC opt-in:", "yes" if usdc is not None else "NO")
    print(f"USDC: {usdc if usdc is not None else 0}")


def cmd_optin() -> None:
    """Opt the payer into USDC if it is not already."""
    addr, mn = load_wallet()
    client = _client()
    info = client.account_info(addr)
    if any(a["asset-id"] == USDC_ASA for a in info.get("assets", [])):
        print("already opted in")
        return
    sk = mnemonic.to_private_key(mn)
    params = client.suggested_params()
    txn = transaction.AssetTransferTxn(sender=addr, sp=params, receiver=addr, amt=0, index=USDC_ASA)
    txid = client.send_transaction(txn.sign(sk))
    transaction.wait_for_confirmation(client, txid, 8)
    print("opted in, tx", txid)


def _decode_offer(response: requests.Response) -> dict:
    """Decode the base64 JSON offer a 402 carries in its PAYMENT-REQUIRED header.

    The body is just "{}" on this backend; the offer lives in the header
    (x402 v2). Padding is re-added because some encoders strip it.
    """
    raw = response.headers.get("payment-required", "")
    if not raw:
        return {}
    padded = raw + "=" * (-len(raw) % 4)
    try:
        return json.loads(base64.b64decode(padded))
    except (ValueError, json.JSONDecodeError) as exc:
        return {"decode_error": str(exc)}


def _print_offer(offer: dict) -> None:
    """Print the parts of a decoded 402 offer a human wants to eyeball."""
    if not offer:
        print("    <no PAYMENT-REQUIRED header>")
        return
    if "decode_error" in offer:
        print("    offer could not be decoded:", offer["decode_error"])
        return
    resource = offer.get("resource", {})
    print("    resource:", resource.get("url"))
    print("    description:", (resource.get("description") or "")[:160])
    for i, opt in enumerate(offer.get("accepts", [])):
        tag = (opt.get("extra") or {}).get("tag")
        print(
            f"    accepts[{i}]: asset={opt.get('asset')} amount={opt.get('amount')} "
            f"network={opt.get('network')} payTo={opt.get('payTo')} tag={tag}"
        )


def _build_http_client(addr: str, mn: str) -> x402HTTPClientSync:
    """The x402 client stack for one payer wallet (imports deferred: heavy)."""
    from x402 import x402ClientSync
    from x402.http.x402_http_client import x402HTTPClientSync
    from x402.mechanisms.avm.exact import ExactAvmScheme

    signer = WorkingAvmSigner(mn)
    assert signer.address == addr
    x = x402ClientSync()
    x.register(MAINNET_CAIP2, ExactAvmScheme(signer=signer, algod_url=ALGOD_URL))
    return x402HTTPClientSync(x)


def _verify_on_chain(txid: str | None, payer: str) -> bool:
    """Independently confirm via the mainnet indexer: sender=payer, receiver=payTo, asset=USDC."""
    if not txid:
        print("  no settlement_tx_id to verify")
        return False
    idx = indexer.IndexerClient("", INDEXER_URL)
    tx = None
    for _ in range(20):
        try:
            tx = idx.transaction(txid)["transaction"]
            break
        except Exception:
            time.sleep(3)
    if tx is None:
        print("  indexer never returned the tx")
        return False
    axfer = tx.get("asset-transfer-transaction", {})
    print("  sender:", tx.get("sender"), "(payer)" if tx.get("sender") == payer else "!!")
    print(
        "  receiver:", axfer.get("receiver"), "(payTo)" if axfer.get("receiver") == PAY_TO else "!!"
    )
    print(
        "  asset:",
        axfer.get("asset-id"),
        "amount:",
        axfer.get("amount"),
        "round:",
        tx.get("confirmed-round"),
    )
    return (
        tx.get("sender") == payer
        and axfer.get("receiver") == PAY_TO
        and axfer.get("asset-id") == USDC_ASA
    )


def cmd_pay() -> None:
    """The original single probe: one paid POST /x402/list, verified on-chain and in search."""
    addr, mn = load_wallet()
    http = _build_http_client(addr, mn)

    url = f"{API}/api/v1/x402/list"
    r1 = requests.post(url, json=PROBE_LISTING, timeout=30)
    print("step 1: unpaid POST ->", r1.status_code)
    if r1.status_code != 402:
        sys.exit(f"expected 402, got {r1.status_code}: {r1.text[:500]}")

    headers, payload = http.handle_402_response(dict(r1.headers), r1.content)
    chosen = payload.accepted if hasattr(payload, "accepted") else None
    print(
        "step 2: payment payload built; asset/amount:",
        getattr(chosen, "asset", "?"),
        getattr(chosen, "amount", "?"),
    )
    if chosen is not None and str(getattr(chosen, "asset", "")) != str(USDC_ASA):
        sys.exit("selector picked a non-USDC option; aborting to keep the probe USDC-only")

    r2 = requests.post(
        url, json=PROBE_LISTING, headers={"Content-Type": "application/json", **headers}, timeout=60
    )
    print("step 3: paid POST ->", r2.status_code)
    print(r2.text[:1000])
    if r2.status_code != 200:
        sys.exit("payment did not settle")
    body = r2.json()
    txid = body.get("settlement_tx_id")
    print("settlement_tx_id:", txid)
    print("payment-response header:", r2.headers.get("payment-response", "<none>")[:200])

    print("step 4: independent on-chain verification via indexer ...")
    ok = _verify_on_chain(txid, addr)

    print("step 5: listing visible in search?")
    items = requests.get(f"{API}/api/v1/x402/search?tag=probe", timeout=30).json().get("items", [])
    if not items:
        items = requests.get(f"{API}/api/v1/x402/search", timeout=30).json().get("items", [])
    visible = any(i.get("url") == PROBE_LISTING["url"] for i in items)
    print("  visible:", visible)
    print("\nRESULT:", "PASS" if ok and visible else "FAIL")
    print(
        "Delist afterwards with the admin route: DELETE /api/v1/admin/x402/listings?url="
        + PROBE_LISTING["url"]
    )


class ProbeAbort(Exception):
    """A step was stopped before any money moved (non-USDC selection, no 402, ...)."""


def _paid_call(
    session: requests.Session,
    http: x402HTTPClientSync | None,
    method: str,
    url: str,
    *,
    json_body: dict | None = None,
    params: dict | None = None,
    dry_run: bool,
) -> tuple[int, dict, str | None]:
    """Unpaid request -> decoded 402 offer -> (unless dry_run) signed retry.

    Returns (status, body, settlement_tx_id). In dry-run mode the status is the
    unpaid one and body/txid are empty. Raises ProbeAbort when the route did
    not answer 402 or the client selector picked a non-USDC option.
    """
    r1 = session.request(method, url, json=json_body, params=params, timeout=30)
    print(f"  unpaid {method} -> {r1.status_code}")
    _print_offer(_decode_offer(r1))
    if r1.status_code != 402:
        raise ProbeAbort(f"expected 402, got {r1.status_code}: {r1.text[:300]}")
    if dry_run:
        return r1.status_code, {}, None

    headers, payload = http.handle_402_response(dict(r1.headers), r1.content)
    chosen = getattr(payload, "accepted", None)
    asset = str(getattr(chosen, "asset", ""))
    print(f"  selector chose asset={asset} amount={getattr(chosen, 'amount', '?')}")
    if asset != str(USDC_ASA):
        raise ProbeAbort(f"selector picked non-USDC asset {asset}; not paying")

    r2 = session.request(
        method,
        url,
        json=json_body,
        params=params,
        headers={"Content-Type": "application/json", **headers},
        timeout=90,
    )
    print(f"  paid {method} -> {r2.status_code}")
    try:
        body = r2.json()
    except ValueError:
        body = {"raw": r2.text[:500]}
    print("  body:", json.dumps(body)[:600])
    txid = body.get("settlement_tx_id") if isinstance(body, dict) else None
    print("  settlement_tx_id:", txid)
    return r2.status_code, body if isinstance(body, dict) else {}, txid


def _file_probe_feature_request(session: requests.Session, *, dry_run: bool) -> str | None:
    """The request id to vote on: a fresh PROBE filing, or in dry-run any existing one.

    Dry-run performs no writes, so it reuses whatever is already on the free
    board (a 402 only needs an existing id; the id's content is irrelevant).
    """
    if dry_run:
        items = (
            session.get(f"{API}/api/v1/x402/features", params={"limit": 1}, timeout=30)
            .json()
            .get("items", [])
        )
        return items[0]["request_id"] if items else None
    r = session.post(
        f"{API}/api/v1/x402/features",
        json={
            "title": "PROBE: self-test request",
            "description": (
                "PROBE: marketplace self-verification filing so the paid vote route can be "
                "exercised once with a labelled payment. Excluded from any volume claim."
            ),
        },
        timeout=30,
    )
    print(f"  free POST /features -> {r.status_code}")
    if r.status_code != 201:
        raise ProbeAbort(f"filing failed: {r.text[:300]}")
    return r.json()["request"]["request_id"]


def cmd_pay_all() -> None:
    """One labelled PROBE payment per live paid route (or unpaid 402s only with --dry-run)."""
    dry_run = "--dry-run" in sys.argv[2:]
    addr, mn = load_wallet()
    http = None if dry_run else _build_http_client(addr, mn)
    session = requests.Session()
    results: list[tuple[str, str, str, str]] = []  # (step, http status, txid, on-chain)

    def run(
        step: str,
        method: str,
        path: str,
        *,
        json_body: dict | None = None,
        params: dict | None = None,
    ) -> None:
        print(f"\n== {step}: {method} {path}")
        try:
            status, _, txid = _paid_call(
                session,
                http,
                method,
                f"{API}{path}",
                json_body=json_body,
                params=params,
                dry_run=dry_run,
            )
        except ProbeAbort as exc:
            print("  ABORTED:", exc)
            results.append((step, "abort", "-", "SKIP"))
            return
        except requests.RequestException as exc:
            print("  REQUEST FAILED:", exc)
            results.append((step, "error", "-", "FAIL"))
            return
        if dry_run:
            results.append((step, str(status), "-", "DRY"))
            return
        verdict = "PASS" if status == 200 and _verify_on_chain(txid, addr) else "FAIL"
        print("  RESULT:", verdict)
        results.append((step, str(status), txid or "-", verdict))

    run(
        "board",
        "POST",
        "/api/v1/x402/board",
        json_body={
            "link": SEARCH_URL,
            "name": "PROBE",
            "pitch": "PROBE: marketplace self-verification placement; excluded from any volume claim. Safe to remove.",
        },
    )
    run(
        "grade",
        "POST",
        "/api/v1/x402/grades",
        json_body={"url": SEARCH_URL, "score": 5, "comment": "PROBE self-test"},
    )
    run("score", "GET", "/api/v1/x402/grades/score", params={"url": SEARCH_URL})

    print("\n== vote: filing the request to vote on")
    try:
        request_id = _file_probe_feature_request(session, dry_run=dry_run)
    except (ProbeAbort, requests.RequestException) as exc:
        print("  ABORTED:", exc)
        request_id = None
    if request_id:
        print("  request_id:", request_id)
        run("vote", "POST", f"/api/v1/x402/features/{request_id}/vote")
    else:
        print("  no request id available; vote step skipped")
        results.append(("vote", "abort", "-", "SKIP"))

    run("demand", "GET", "/api/v1/x402/features/demand", params={"limit": 25})

    print("\n" + "=" * 72)
    print(f"{'step':<8} {'http':<6} {'settlement_tx_id':<54} result")
    for step, status, txid, verdict in results:
        print(f"{step:<8} {status:<6} {txid:<54} {verdict}")
    print("=" * 72)
    if not dry_run:
        print(
            "Clean-up: the board placement and grade are labelled PROBE; remove via the admin routes if desired."
        )


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    {
        "gen": cmd_gen,
        "status": cmd_status,
        "optin": cmd_optin,
        "pay": cmd_pay,
        "pay-all": cmd_pay_all,
    }.get(cmd, lambda: sys.exit(__doc__))()
