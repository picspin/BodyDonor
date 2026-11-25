#!/usr/bin/env python3
"""review_report_validate.py — 审查报告轻量结构校验（wrapper 与 checker 共用一份）。

校验 reviews/review-<sha>.json 的结构：
- 顶层必须是 dict；
- review_sha 必须是与 HEAD 比对的字符串（非空）；
- findings 必须是 list，每条必须是 dict；
- 每条 finding 必须六字段齐全且非 None：severity/file/evidence/suggestion/reproduction
  为 str，line 为 int；resolved_in_commit 若存在须为 str 或 null。

CLI：review_report_validate.py <report.json> [expected_sha]
  校验通过 exit 0；任何结构问题 exit 1（stderr 给原因）。expected_sha 给出时
  同时校验报告内 review_sha 与之一致。
"""

import json
import sys

FINDING_STR_FIELDS = ("severity", "file", "evidence", "suggestion", "reproduction")


def validate_report(data, expected_sha=None):
    """校验报告结构，合法返回 None，非法返回错误字符串。"""
    if not isinstance(data, dict):
        return "report not a JSON object"
    review_sha = data.get("review_sha")
    if not isinstance(review_sha, str) or not review_sha:
        return "review_sha must be a non-empty string"
    if expected_sha is not None and review_sha != expected_sha:
        return f"review_sha={review_sha} does not match expected {expected_sha}"
    findings = data.get("findings")
    if not isinstance(findings, list):
        return "findings must be a list"
    for i, f in enumerate(findings):
        err = _validate_finding(i, f)
        if err:
            return err
    ric = data.get("resolved_in_commit")
    if ric is not None and not isinstance(ric, str):
        return "resolved_in_commit must be a string or null"
    return None


def _validate_finding(i, f):
    if not isinstance(f, dict):
        return f"findings[{i}] not an object"
    for k in FINDING_STR_FIELDS:
        v = f.get(k)
        if v is None:
            return f"findings[{i}] missing field: {k}"
        if not isinstance(v, str):
            return f"findings[{i}].{k} must be a string"
    line = f.get("line")
    if line is None:
        return f"findings[{i}] missing field: line"
    if not isinstance(line, int) or isinstance(line, bool):
        return f"findings[{i}].line must be an int"
    return None


def main() -> int:
    if len(sys.argv) not in (2, 3):
        print(f"usage: {sys.argv[0]} <report.json> [expected_sha]", file=sys.stderr)
        return 2
    try:
        with open(sys.argv[1]) as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"INVALID: report not found: {sys.argv[1]}", file=sys.stderr)
        return 1
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
        print(f"INVALID: report not readable JSON: {sys.argv[1]} ({e})", file=sys.stderr)
        return 1
    err = validate_report(data, sys.argv[2] if len(sys.argv) == 3 else None)
    if err:
        print(f"INVALID: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
