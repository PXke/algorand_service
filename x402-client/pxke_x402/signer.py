"""The AVM client signer, using the pattern that actually works.

The `x402-avm==2.0.2` package's own `ClientAvmSigner` docstring example is
wrong in three places -- it threads `algosdk.encoding.*` helpers that expect
base64 *strings* through an interface that hands it raw msgpack *bytes*. See
`docs/x402-facilitator.md` in the PXke Algorand backend repo for the full
root-cause writeup. This is the working implementation, copied from the
mainnet-proven `backend/scripts/x402_mainnet_probe.py`: unpack the raw
msgpack bytes ourselves before handing a dict to `msgpack_decode` (which
special-cases dict input and skips its own base64 step), sign with the
base64 *string* form of the secret key, and unwrap `msgpack_encode`'s base64
string back to raw bytes before returning.
"""

from __future__ import annotations

import base64

import algosdk
import msgpack
from algosdk import mnemonic


class WorkingAvmSigner:
    """A `ClientAvmSigner`-shaped signer for one wallet, derived from its mnemonic."""

    def __init__(self, mnemonic_phrase: str) -> None:
        """Derive the signing key and address from a 25-word mnemonic phrase."""
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
