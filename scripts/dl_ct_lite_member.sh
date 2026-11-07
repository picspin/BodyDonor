#!/usr/bin/env bash
# CT-Lite 单 member 下载（zip 范围请求绕过全量 22.6GB）。
# 方法：HF xet-bridge CDN 支持 Range；fetch EOCD -> CD（分块）-> 按 member 的
# local header offset+csize Range 抓取 -> raw deflate 解压 -> CRC 校验。
# 由 scripts/dl_ct_lite.py 调用；不直接使用。
set -euo pipefail
