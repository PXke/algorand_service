from __future__ import annotations

from app.modules.auth.utils.caip122 import Caip122Message
from app.modules.auth.utils.siwa_message import prepare_siwa_from_caip122


def test_prepare_siwa_message_eip4361_shape() -> None:
    caip122 = Caip122Message(
        domain="arc60.io",
        account_address="BYVBFXCGJLDU5Q7POFA2G4CLAGUBWRU3TOKDPNQG57D44KW6CVY3FPIXRM",
        uri="https://arc60.io",
        chain_id="algorand:SGO1GKSzyE7IEPItTxCByw9x8FmnrCDe",
        nonce="A4nEQYY3Ss9sCkTMwIIZui5VeUS5Y1HAQDK2+ivNtX8=",
        statement="We are requesting you to sign this message to authenticate to arc60.io",
        issued_at="2021-12-31T23:59:59Z",
        version="1",
    )
    msg = prepare_siwa_from_caip122(caip122, wallet_connect_chain_id=416002)
    assert "arc60.io wants you to sign in with your Algorand account:" in msg
    assert "BYVBFXCGJLDU5Q7POFA2G4CLAGUBWRU3TOKDPNQG57D44KW6CVY3FPIXRM" in msg
    assert "Chain ID: 416002" in msg
    assert "Nonce: A4nEQYY3Ss9sCkTMwIIZui5VeUS5Y1HAQDK2+ivNtX8=" in msg
