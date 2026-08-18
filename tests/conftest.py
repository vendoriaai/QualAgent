"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def global_dir(tmp_path: Path) -> Path:
    """Isolated global config directory for a test."""
    gdir = tmp_path / "home" / ".qualagent"
    gdir.mkdir(parents=True)
    return gdir
