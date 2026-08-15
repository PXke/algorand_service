"""query_uk_companies_house: a free, UK-specific alternative to query_corporate_registry (OpenCorporates), which is disabled entirely without a paid token -- verifies a "registered UK company" claim via Companies House's own free API."""

from __future__ import annotations

import httpx
import pytest

from app.modules.ai import investigative_tools as it


def test_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """No COMPANIES_HOUSE_API_KEY configured -- a clear error, not a bare 401 from the API."""
    monkeypatch.delenv("COMPANIES_HOUSE_API_KEY", raising=False)
    result = it.query_uk_companies_house(company_name="Brale")
    assert "error" in result


def test_requires_name_or_number(monkeypatch: pytest.MonkeyPatch) -> None:
    """A key alone isn't enough -- some query input is required."""
    monkeypatch.setenv("COMPANIES_HOUSE_API_KEY", "test-key")
    result = it.query_uk_companies_house()
    assert "error" in result


def test_lookup_by_number_returns_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    """A company_number hits the direct profile endpoint and returns the shaped fields."""
    monkeypatch.setenv("COMPANIES_HOUSE_API_KEY", "test-key")
    profile = {
        "company_name": "BRALE UK LTD",
        "company_number": "12345678",
        "company_status": "active",
        "type": "ltd",
        "date_of_creation": "2021-03-01",
        "sic_codes": ["64110"],
        "registered_office_address": {"locality": "London"},
    }
    resp = httpx.Response(
        200,
        json=profile,
        request=httpx.Request("GET", "https://api.company-information.service.gov.uk/company/12345678"),
    )
    monkeypatch.setattr(
        "app.core.net_guard.guarded_get", lambda *_a, **_kw: resp,
    )
    result = it.query_uk_companies_house(company_number="12345678")
    assert result["name"] == "BRALE UK LTD"
    assert result["status"] == "active"
    assert result["sic_codes"] == ["64110"]


def test_lookup_by_number_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 404 from Companies House is reported plainly, not as a raised exception."""
    monkeypatch.setenv("COMPANIES_HOUSE_API_KEY", "test-key")
    resp = httpx.Response(
        404,
        request=httpx.Request("GET", "https://api.company-information.service.gov.uk/company/00000000"),
    )
    monkeypatch.setattr(
        "app.core.net_guard.guarded_get", lambda *_a, **_kw: resp,
    )
    result = it.query_uk_companies_house(company_number="00000000")
    assert result["error"] == "no company with this number"


def test_lookup_by_name_returns_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    """A company_name search returns shaped candidates from the search endpoint."""
    monkeypatch.setenv("COMPANIES_HOUSE_API_KEY", "test-key")
    resp = httpx.Response(
        200,
        json={
            "items": [
                {
                    "title": "BRALE UK LTD",
                    "company_number": "12345678",
                    "company_status": "active",
                    "date_of_creation": "2021-03-01",
                    "address_snippet": "London, UK",
                }
            ]
        },
        request=httpx.Request("GET", "https://api.company-information.service.gov.uk/search/companies"),
    )
    monkeypatch.setattr(
        "app.core.net_guard.guarded_get", lambda *_a, **_kw: resp,
    )
    result = it.query_uk_companies_house(company_name="Brale")
    assert result["matches"] == 1
    assert result["companies"][0]["number"] == "12345678"


def test_registers_only_when_key_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """query_uk_companies_house joins the entity-OSINT toolset only when COMPANIES_HOUSE_API_KEY is set -- otherwise the API returns a permanent 401, so offering it just burns a writer turn."""
    monkeypatch.delenv("COMPANIES_HOUSE_API_KEY", raising=False)
    monkeypatch.delenv("OPENCORPORATES_API_TOKEN", raising=False)
    schemas, handlers = it.investigative_tools(include_entity_osint=True)
    names = {s["function"]["name"] for s in schemas}
    assert "query_uk_companies_house" not in names
    assert "query_uk_companies_house" not in handlers

    monkeypatch.setenv("COMPANIES_HOUSE_API_KEY", "test-key")
    schemas, handlers = it.investigative_tools(include_entity_osint=True)
    names = {s["function"]["name"] for s in schemas}
    assert "query_uk_companies_house" in names
    assert "query_uk_companies_house" in handlers
