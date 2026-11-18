"""配置加载：config/settings.yaml -> 强类型 Settings 对象。

配置化限制（proposal §4.3）：deployment_mode 与 max_upload_mb 是 config diff，不是代码分叉。
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "settings.yaml"

DeploymentMode = Literal["cloud_demo", "on_prem"]


class CloudDemoLimits(BaseModel):
    max_upload_mb: int = 100
    retention_hours: int = 24
    pixel_egress: str = "allow_public_only"


class OnPremLimits(BaseModel):
    max_upload_mb: int | None = None
    ingest: list[str] = Field(default_factory=lambda: ["upload", "folder_import"])


class Limits(BaseModel):
    cloud_demo: CloudDemoLimits = Field(default_factory=CloudDemoLimits)
    on_prem: OnPremLimits = Field(default_factory=OnPremLimits)


class ExtractLimits(BaseModel):
    max_compression_ratio: int = 100
    max_nested_zip_depth: int = 3
    max_file_count: int = 10000


class TeacherConfig(BaseModel):
    engine: str = "totalsegmentator"
    allowed_tasks: list[str] = Field(default_factory=list)
    blocked_tasks: list[str] = Field(default_factory=list)


class NnInteractiveConfig(BaseModel):
    role: str = "interactive_correction_only"
    outputs_enter_commercial_training: bool = False


class ExperimentalTextPrompt(BaseModel):
    enabled: bool = False


class StorageConfig(BaseModel):
    backend: Literal["local_fs"] = "local_fs"


class QueueConfig(BaseModel):
    backend: Literal["inprocess"] = "inprocess"


class DatabaseConfig(BaseModel):
    url: str = "sqlite:///./bodydonor.db"


class Settings(BaseModel):
    deployment_mode: DeploymentMode = "on_prem"
    data_dir: Path | None = None
    limits: Limits = Field(default_factory=Limits)
    extract_limits: ExtractLimits = Field(default_factory=ExtractLimits)
    teacher: TeacherConfig = Field(default_factory=TeacherConfig)
    nninteractive: NnInteractiveConfig = Field(default_factory=NnInteractiveConfig)
    experimental_text_prompt: ExperimentalTextPrompt = Field(
        default_factory=ExperimentalTextPrompt
    )
    storage: StorageConfig = Field(default_factory=StorageConfig)
    queue: QueueConfig = Field(default_factory=QueueConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)

    @property
    def resolved_data_dir(self) -> Path:
        """数据目录：显式配置优先，否则 ~/data/BodyDonor（禁 /mnt/c，proposal §10）。"""
        if self.data_dir is not None:
            p = Path(self.data_dir)
        else:
            p = Path.home() / "data" / "BodyDonor"
        if "/mnt/c" in p.parts or str(p).startswith("/mnt/c"):
            raise ValueError("数据目录禁止放在 /mnt/c（9P 协议读 DICOM 慢一个量级）")
        return p

    @property
    def max_upload_mb(self) -> int | None:
        """当前部署形态的上传上限：仅 cloud_demo 有限制，on_prem 为 None。"""
        return getattr(self.limits, self.deployment_mode).max_upload_mb


def load_settings(path: Path | None = None) -> Settings:
    config_path = path or DEFAULT_CONFIG_PATH
    if not config_path.exists():
        return Settings()
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return Settings.model_validate(raw)
