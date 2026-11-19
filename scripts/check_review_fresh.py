#!/usr/bin/env python3
"""check_review_fresh.py — 校验审查报告是否仍绑定当前 HEAD。

用法：check_review_fresh.py reviews/review-<sha>.json

读报告内的 review_sha 与当前 HEAD 比对：
- 一致且 stale 为 false → 打印 FRESH，exit 0；
- 不一致或 stale 为 true → 打印 STALE（含报告 sha 与当前 HEAD），exit 1。

引用审查结论前必须跑本脚本核验；HEAD 一变旧报告即失效。
"""

import json
import subprocess
import sys


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
    except json.JSONDecodeError as e:
        print(f"STALE: report not valid JSON: {sys.argv[1]} ({e})")
        return 1
    review_sha = report.get("review_sha", "")
    head = current_head()
    if report.get("stale") is True:
        print(f"STALE: report stale flag set (review_sha={review_sha}, HEAD={head})")
        return 1
    if review_sha != head:
        print(f"STALE: review_sha={review_sha}, HEAD={head}")
        return 1
    print("FRESH")
    return 0


if __name__ == "__main__":
    sys.exit(main())
