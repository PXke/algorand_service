from __future__ import annotations

from app.modules.auth.utils.caip122 import Caip122Message


def prepare_siwa_message(
    *,
    domain: str,
    account_address: str,
    uri: str,
    chain_id: int,
    nonce: str,
    statement: str | None = None,
    version: str = "1",
    issued_at: str | None = None,
    expiration_time: str | None = None,
    not_before: str | None = None,
    request_id: str | None = None,
    resources: list[str] | None = None,
    scheme: str | None = None,
) -> str:
    """EIP-4361 / SIWA human-readable message (compatible with @avmkit/siwa)."""
    if statement and "\n" in statement:
        msg = "statement must not contain newlines"
        raise ValueError(msg)

    header_prefix = f"{scheme}://{domain}" if scheme else domain
    header = f"{header_prefix} wants you to sign in with your Algorand account:"
    prefix = f"{header}\n{account_address}"

    if statement:
        prefix = f"{prefix}\n\n{statement}\n"

    suffix_parts = [
        f"URI: {uri}",
        f"Version: {version}",
        f"Chain ID: {chain_id}",
        f"Nonce: {nonce}",
    ]

    if issued_at:
        suffix_parts.append(f"Issued At: {issued_at}")
    if expiration_time:
        suffix_parts.append(f"Expiration Time: {expiration_time}")
    if not_before:
        suffix_parts.append(f"Not Before: {not_before}")
    if request_id:
        suffix_parts.append(f"Request ID: {request_id}")
    if resources:
        resource_lines = "\n".join(f"- {r}" for r in resources)
        suffix_parts.append(f"Resources:\n{resource_lines}")

    return f"{prefix}\n{chr(10).join(suffix_parts)}"


def prepare_siwa_from_caip122(caip122: Caip122Message, *, wallet_connect_chain_id: int) -> str:
    """Build SIWA display string from CAIP-122 fields."""
    return prepare_siwa_message(
        domain=caip122.domain,
        account_address=caip122.account_address,
        uri=caip122.uri,
        chain_id=wallet_connect_chain_id,
        nonce=caip122.nonce,
        statement=caip122.statement,
        version=caip122.version,
        issued_at=caip122.issued_at,
        expiration_time=caip122.expiration_time,
        not_before=caip122.not_before,
        request_id=caip122.request_id,
        resources=caip122.resources or None,
    )
