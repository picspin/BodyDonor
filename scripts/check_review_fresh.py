#!/usr/bin/env python3
"""check_review_fresh.py — 校验审查报告是否仍绑定当前 HEAD。

用法：check_review_fresh.py reviews/review-<sha>.json

读报告内的 review_sha 与当前 HEAD 比对，并共用 review_report_validate 校验结构：
- 一致、stale 显式为 false、结构合法 → 打印 FRESH，exit 0；
- 不一致、stale 非 False（缺失/null/0/"false" 等）、结构损坏 → 打印 STALE，exit 1。

FRESH 只表示报告完成且绑定当前提交，不代表所有问题已解决或允许合并。
引用审查结论前必须跑本脚本核验；HEAD 一变旧报告即失效。
"""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from review_report_validate import validate_report


def current_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} reviews/review-<sha>.json", file=sys.stderr)
        return 2
    try:
        with open(sys.argv[1]) as f:
            report = json.load(f)
    except FileNotFoundError:
        print(f"STALE: report not found: {sys.argv[1]}")
        return 1
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
        print(f"STALE: report not readable JSON: {sys.argv[1]} ({e})")
        return 1
    if not isinstance(report, dict):
        print(f"STALE: report not a JSON object: {sys.argv[1]}")
        return 1
    review_sha = report.get("review_sha", "")
    head = current_head()
    err = validate_report(report)
    if err:
        print(f"STALE: invalid report structure ({err}): {sys.argv[1]}")
        return 1
    if report.get("stale") is not False:
        print(f"STALE: stale flag not explicitly false (review_sha={review_sha}, HEAD={head})")
        return 1
    if review_sha != head:
        print(f"STALE: review_sha={review_sha}, HEAD={head}")
        return 1
    print("FRESH")
    return 0


if __name__ == "__main__":
    sys.exit(main())
