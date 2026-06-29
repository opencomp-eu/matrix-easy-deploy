"""Pytest fixtures for matrix-easy-deploy tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.helpers.project_tree import build_minimal_project


@pytest.fixture(autouse=True)
def disable_mas_docker_keygen(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid 60s docker pull timeouts when unit tests generate MAS signing keys."""
    monkeypatch.setenv("MED_MAS_USE_DOCKER_GENERATE", "0")


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    return build_minimal_project(tmp_path, preset="full")
