"""Shared pytest fixtures for the public test suite."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from dexter_sync.models import Resident
from dexter_sync.provider_client import FailurePlan, MockCareProvider
from dexter_sync.repository import InMemoryRepository

DATA_DIR = Path(__file__).parent.parent / "data"


@pytest.fixture
def data_dir() -> Path:
    return DATA_DIR


@pytest.fixture
def repository() -> InMemoryRepository:
    return InMemoryRepository()


@pytest.fixture
def provider(data_dir: Path) -> MockCareProvider:
    """Provider configured with the two-page happy fixture set."""
    return MockCareProvider(
        data_dir=data_dir,
        page_files=["provider_page_1.json", "provider_page_2.json"],
    )


@pytest.fixture
def single_page_provider(data_dir: Path) -> MockCareProvider:
    """Provider configured with only page_1 — clean happy path."""
    return MockCareProvider(
        data_dir=data_dir,
        page_files=["provider_page_1.json"],
    )


@pytest.fixture
def errors_provider(data_dir: Path) -> MockCareProvider:
    """Provider configured to return the page_with_errors fixture."""
    return MockCareProvider(
        data_dir=data_dir,
        page_files=["provider_page_with_errors.json"],
    )


@pytest.fixture
def failure_plan() -> FailurePlan:
    """Empty failure plan; tests mutate it in-place."""
    return FailurePlan()


def seed_repo_from_fixture(repo: InMemoryRepository, fixture_path: Path) -> None:
    """Pre-seed a repository from existing_residents.json (or similar)."""
    with open(fixture_path, encoding="utf-8") as f:
        data = json.load(f)
    for raw in data["residents"]:
        repo.upsert_resident(
            Resident(
                provider_id=raw["provider_id"],
                full_name=raw["full_name"],
                date_of_birth=raw.get("date_of_birth"),
                room=raw.get("room"),
                care_level=raw.get("care_level"),
                updated_at=datetime.fromisoformat(raw["updated_at"]),
                is_active=raw.get("is_active", True),
            )
        )


@pytest.fixture
def seeded_repository(repository: InMemoryRepository, data_dir: Path) -> InMemoryRepository:
    seed_repo_from_fixture(repository, data_dir / "existing_residents.json")
    return repository
