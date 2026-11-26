#!/usr/bin/env python
"""verify-loop Step 1 launcher：FastAPI app 绑定 scratch DB + tmp data dir。

uvicorn 入口（`uvicorn chain_app:app`）。在 import bodydonor.app 前把
config.DEFAULT_CONFIG_PATH 指向 $VERIFY_LOOP_TMP/settings.yaml（run_chain.sh
写入：data_dir + database.url 全在仓外 tmp）——app 模块级构造的 queue/storage
（SqliteJobsQueue init_db、LocalFsStorage mkdir）即绑定 tmp 路径，绝不碰真实
bodydonor.db / ~/data/BodyDonor（DESIGN.md 决定 3）。

fail-closed：VERIFY_LOOP_TMP 未设即拒绝启动（不静默回落真实配置）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_tmp = os.environ.get("VERIFY_LOOP_TMP", "")
if not _tmp:
    print("ERROR: VERIFY_LOOP_TMP 未设（fail-closed，不回落真实配置）", file=sys.stderr)
    raise SystemExit(2)
_yaml = Path(_tmp) / "settings.yaml"
if not _yaml.exists():
    print(f"ERROR: settings.yaml 不存在：{_yaml}", file=sys.stderr)
    raise SystemExit(2)

import bodydonor.config as _cfg  # noqa: E402

_cfg.DEFAULT_CONFIG_PATH = _yaml

from bodydonor import app as _app_module  # noqa: E402

app = _app_module.app
