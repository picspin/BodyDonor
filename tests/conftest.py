"""测试共享 fixture。"""

from __future__ import annotations

from pathlib import Path

import pytest

from bodydonor.config import Settings


@pytest.fixture
def settings() -> Settings:
    """默认 settings（on_prem，标准白名单）。"""
    return Settings()


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data" / "BodyDonor"
