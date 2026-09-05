"""Unit tests for company portfolio id helpers (keep in sync with TS)."""

from __future__ import annotations

import pytest

from mt5_portfolio_merge import (
    LEGACY_ALL_FAVORITES_PORTFOLIO_ID,
    MIGRATION_DEFAULT_COMPANY,
    build_company_portfolio_id,
    hash_normalized_company_name,
    is_company_portfolio_id,
    resolve_favorite_company_for_portfolio,
    slugify_company_name,
)


def test_slugify_company_name() -> None:
    assert (
        slugify_company_name("Tradeslide Trading Tech Limited")
        == "tradeslide-trading-tech-limited"
    )
    assert slugify_company_name("  FTMO  ") == "ftmo"
    assert slugify_company_name("Foo & Bar, Inc.") == "foo-bar-inc"
    assert slugify_company_name("---Foo---Bar---") == "foo-bar"


def test_build_company_portfolio_id() -> None:
    digest = hash_normalized_company_name("Tradeslide Trading Tech Limited")
    assert (
        build_company_portfolio_id("Tradeslide Trading Tech Limited")
        == f"company:tradeslide-trading-tech-limited-{digest}"
    )
    assert build_company_portfolio_id("FTMO") == build_company_portfolio_id("ftmo")
    assert build_company_portfolio_id("Foo Bar") != build_company_portfolio_id("Foo-Bar")
    assert slugify_company_name("Foo Bar") == slugify_company_name("Foo-Bar")
    with pytest.raises(ValueError, match="empty portfolio slug"):
        build_company_portfolio_id("   ")
    with pytest.raises(ValueError, match="empty portfolio slug"):
        build_company_portfolio_id("@@@")
    with pytest.raises(ValueError, match="empty portfolio slug"):
        build_company_portfolio_id("日本")


def test_build_company_portfolio_id_non_ascii_with_slug() -> None:
    portfolio_id = build_company_portfolio_id("Café Broker")
    assert portfolio_id.startswith("company:caf-broker-")
    assert is_company_portfolio_id(portfolio_id) is True


def test_is_company_portfolio_id() -> None:
    assert is_company_portfolio_id(build_company_portfolio_id("FTMO")) is True
    assert is_company_portfolio_id(LEGACY_ALL_FAVORITES_PORTFOLIO_ID) is False
    assert is_company_portfolio_id("company:") is False
    assert is_company_portfolio_id("company:../etc") is False
    assert is_company_portfolio_id("company:FTMO") is False
    assert is_company_portfolio_id("company:foo--bar") is False
    assert is_company_portfolio_id("company:foo-") is False


def test_resolve_favorite_company_for_portfolio() -> None:
    assert resolve_favorite_company_for_portfolio("FTMO") == "FTMO"
    assert resolve_favorite_company_for_portfolio("  FTMO  ") == "FTMO"
    assert resolve_favorite_company_for_portfolio(None) == MIGRATION_DEFAULT_COMPANY
    assert resolve_favorite_company_for_portfolio("") == MIGRATION_DEFAULT_COMPANY
