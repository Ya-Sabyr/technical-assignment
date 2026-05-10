"""Public tests for the mock provider client.

These verify the simulator itself behaves as documented. They should pass
without any candidate changes — they exist so candidates can trust the
fixture before debugging their sync logic.
"""
from __future__ import annotations

import pytest

from dexter_sync.exceptions import (
    PermanentProviderError,
    RateLimitError,
    TransientProviderError,
)
from dexter_sync.provider_client import FailurePlan, MockCareProvider


def test_pagination_returns_cursor(data_dir):
    provider = MockCareProvider(
        data_dir=data_dir,
        page_files=["provider_page_1.json", "provider_page_2.json"],
    )
    page_1 = provider.list_residents(cursor=None)
    assert len(page_1["residents"]) == 5
    assert page_1["next_cursor"] == "page_2"

    page_2 = provider.list_residents(cursor=page_1["next_cursor"])
    assert len(page_2["residents"]) == 4
    assert page_2["next_cursor"] is None


def test_rate_limit_then_success(data_dir):
    plan = FailurePlan(rate_limit_failures_per_cursor={None: 1})
    provider = MockCareProvider(
        data_dir=data_dir,
        page_files=["provider_page_1.json"],
        failure_plan=plan,
    )
    with pytest.raises(RateLimitError):
        provider.list_residents(cursor=None)
    page = provider.list_residents(cursor=None)
    assert len(page["residents"]) == 5


def test_permanent_error_simulation(data_dir):
    plan = FailurePlan(permanent_failure_cursors={None})
    provider = MockCareProvider(
        data_dir=data_dir,
        page_files=["provider_page_1.json"],
        failure_plan=plan,
    )
    with pytest.raises(PermanentProviderError):
        provider.list_residents(cursor=None)


def test_transient_error_simulation(data_dir):
    plan = FailurePlan(transient_failures_per_cursor={None: 2})
    provider = MockCareProvider(
        data_dir=data_dir,
        page_files=["provider_page_1.json"],
        failure_plan=plan,
    )
    with pytest.raises(TransientProviderError):
        provider.list_residents(cursor=None)
    with pytest.raises(TransientProviderError):
        provider.list_residents(cursor=None)
    page = provider.list_residents(cursor=None)
    assert len(page["residents"]) == 5
