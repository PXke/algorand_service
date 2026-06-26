from __future__ import annotations

import pytest

from app.modules.chain.models import IndexedTransaction
from app.modules.chain.repository import FakeChainRepository, set_chain_repository


@pytest.fixture
def fake_chain_repo() -> FakeChainRepository:
    repo = FakeChainRepository()
    set_chain_repository(repo)
    yield repo
    set_chain_repository(None)


@pytest.fixture
def sample_tx() -> IndexedTransaction:
    return IndexedTransaction(
        txid="A" * 52,
        round=42,
        intra=0,
        sender="B" * 58,
        txn_type="pay",
        txn_json="{}",
    )
