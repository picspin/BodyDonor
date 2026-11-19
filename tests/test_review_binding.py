"""③ 审查绑定 commit 的确定性行为测试。

覆盖：
1. check_review_fresh.py 读侧校验：fresh exit 0；HEAD 变化 / stale=true exit 非 0 且打印 STALE。
2. 包装脚本产出的 JSON 含完整 schema（review_sha/region/findings/resolved_in_commit/stale），
   findings 字段与现有格式一致（severity/file/line/evidence/suggestion/reproduction）。
3. 产出时 HEAD 移动即标 stale（fake_claude 替身在落盘后移动 HEAD）。
scratch git 仓测试，不动本 worktree 的 HEAD。
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
WRAPPER = REPO_ROOT / "scripts" / "deep_review_from_main.sh"
FRESH_CHECK = REPO_ROOT / "scripts" / "check_review_fresh.py"

MAIN_RULES = "# Deep Reviewer (main)\n\n4. 测试 MAIN-MARKER: 新增行为必须有测试覆盖。\n"
WORK_RULES = "# Deep Reviewer (worktree)\n\n4. 测试 WORK-MARKER: 无需测试覆盖。\n"

FULL_FINDING = {
    "severity": "warning",
    "file": "src/x.py",
    "line": 12,
    "evidence": "e",
    "suggestion": "s",
    "reproduction": "r",
}


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    )
    return out.stdout


def _clean_env() -> dict:
    env = os.environ.copy()
    for var in ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_OBJECT_DIRECTORY"):
        env.pop(var, None)
    return env


def _make_scratch(tmp_path: Path, with_main_rules: bool = True) -> Path:
    repo = tmp_path / "scratch"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    rule_path = repo / ".claude" / "agents" / "deep-reviewer.md"
    rule_path.parent.mkdir(parents=True)
    if with_main_rules:
        rule_path.write_text(MAIN_RULES)
    (repo / "f.txt").write_text("x\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "main base")
    return repo


def _add_work_branch(repo: Path) -> str:
    _git(repo, "checkout", "-b", "work")
    (repo / ".claude" / "agents" / "deep-reviewer.md").write_text(WORK_RULES)
    (repo / "g.txt").write_text("y\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "worktree-relaxed")
    return _git(repo, "rev-parse", "HEAD").strip()


@pytest.fixture()
def fake_claude(tmp_path: Path):
    """PATH 替身：模拟 claude -p 写最小格式 findings JSON；FAKE_MODE 控制行为。"""
    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    stdin_path = tmp_path / "fake-claude-stdin.md"
    fake = bindir / "claude"
    fake.write_text(
        "#!/bin/sh\n"
        'cat > "${FAKE_STDIN:?FAKE_STDIN required}"\n'
        'sha=$(git rev-parse HEAD)\n'
        'mkdir -p reviews\n'
        'case "${FAKE_MODE:-write}" in\n'
        "  nowrite) exit 0 ;;\n"
        '  movelate) printf \'{"review_sha":"%s","findings":[],"region":"TBD",'
        '"resolved_in_commit":null}\' "$sha" > "reviews/review-$sha.json" &&'
        ' git -c user.email=t@example.com -c user.name=t commit -q --allow-empty -m "moved";;\n'
        '  missingfield) printf \'{"review_sha":"%s","findings":[{"severity":"warning",'
        '"file":"src/x.py","line":12}]}\' "$sha" > "reviews/review-$sha.json" ;;\n'
        '  nullfield) printf \'{"review_sha":"%s","findings":[{"severity":"warning",'
        '"file":"src/x.py","line":12,"evidence":null,"suggestion":"s",'
        '"reproduction":"r"}]}\' "$sha" > "reviews/review-$sha.json" ;;\n'
        '  *) printf \'{"review_sha":"%s","findings":[{"severity":"warning",'
        '"file":"src/x.py","line":12,"evidence":"e","suggestion":"s",'
        '"reproduction":"r"}]}\' "$sha" > "reviews/review-$sha.json" ;;\n'
        "esac\n"
    )
    fake.chmod(0o755)
    return bindir, stdin_path


def _run_wrapper(
    repo: Path, bindir: Path, stdin_path: Path, *args: str, fake_mode: str = "write"
):
    env = _clean_env()
    env["PATH"] = f"{bindir}:{env['PATH']}"
    env["FAKE_STDIN"] = str(stdin_path)
    env["FAKE_MODE"] = fake_mode
    return subprocess.run(
        ["bash", str(WRAPPER), *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )


def _run_fresh_check(repo: Path, report: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["python3", str(FRESH_CHECK), str(report)],
        cwd=repo,
        capture_output=True,
        text=True,
    )


# --- check_review_fresh.py 读侧校验 -----------------------------------------


def test_fresh_check_fresh_report_exits_zero(tmp_path):
    repo = _make_scratch(tmp_path)
    head = _git(repo, "rev-parse", "HEAD").strip()
    report = repo / "reviews" / f"review-{head}.json"
    report.parent.mkdir()
    report.write_text(json.dumps({"review_sha": head, "findings": [], "stale": False}))

    r = _run_fresh_check(repo, report)

    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.strip() == "FRESH"


def test_fresh_check_head_moved_exits_nonzero(tmp_path):
    """HEAD 变化后同一文件 exit 非 0 且打印 STALE（含两个 sha）。"""
    repo = _make_scratch(tmp_path)
    old_head = _git(repo, "rev-parse", "HEAD").strip()
    report = repo / "reviews" / f"review-{old_head}.json"
    report.parent.mkdir()
    report.write_text(json.dumps({"review_sha": old_head, "findings": [], "stale": False}))
    _git(repo, "commit", "--allow-empty", "-m", "moved after review")

    r = _run_fresh_check(repo, report)

    assert r.returncode != 0
    assert "STALE" in r.stdout
    new_head = _git(repo, "rev-parse", "HEAD").strip()
    assert old_head in r.stdout
    assert new_head in r.stdout


def test_fresh_check_stale_flag_exits_nonzero_even_if_sha_matches(tmp_path):
    repo = _make_scratch(tmp_path)
    head = _git(repo, "rev-parse", "HEAD").strip()
    report = repo / "reviews" / f"review-{head}.json"
    report.parent.mkdir()
    report.write_text(json.dumps({"review_sha": head, "findings": [], "stale": True}))

    r = _run_fresh_check(repo, report)

    assert r.returncode != 0
    assert "STALE" in r.stdout


def test_fresh_check_missing_report_exits_nonzero(tmp_path):
    repo = _make_scratch(tmp_path)

    r = _run_fresh_check(repo, repo / "reviews" / "review-none.json")

    assert r.returncode != 0
    assert "STALE" in r.stdout


def test_fresh_check_invalid_json_exits_nonzero(tmp_path):
    repo = _make_scratch(tmp_path)
    report = repo / "reviews" / "review-bad.json"
    report.parent.mkdir()
    report.write_text("not json")

    r = _run_fresh_check(repo, report)

    assert r.returncode != 0
    assert "STALE" in r.stdout


def test_fresh_check_non_object_json_exits_nonzero(tmp_path):
    """JSON 顶层非 object（如 []、null）必须判 STALE，不裸 traceback。"""
    repo = _make_scratch(tmp_path)
    report = repo / "reviews" / "review-arr.json"
    report.parent.mkdir()
    report.write_text("[]")

    r = _run_fresh_check(repo, report)

    assert r.returncode != 0
    assert "STALE" in r.stdout


# --- 包装脚本产出 schema ------------------------------------------------------


def test_wrapper_output_full_schema(tmp_path, fake_claude):
    """包装脚本写出的 JSON 含 review_sha/region/findings/resolved_in_commit/stale 五键，
    findings 字段与现有格式一致（severity/file/line/evidence/suggestion/reproduction）。"""
    repo = _make_scratch(tmp_path)
    _add_work_branch(repo)
    bindir, stdin_path = fake_claude

    r = _run_wrapper(repo, bindir, stdin_path, "work")
    assert r.returncode == 0, r.stdout + r.stderr
    head = _git(repo, "rev-parse", "HEAD").strip()
    out = repo / "reviews" / f"review-{head}.json"
    data = json.loads(out.read_text())

    assert data["review_sha"] == head
    assert data["region"] == "work", "region=传给包装脚本的范围字符串"
    assert data["stale"] is False
    findings = data["findings"]
    assert len(findings) == 1
    for key in ("severity", "file", "line", "evidence", "suggestion", "reproduction"):
        assert key in findings[0], f"findings 字段缺失: {key}"
    assert findings[0]["resolved_in_commit"] is None, "未解决时为 null"


def test_wrapper_marks_stale_when_head_moved_during_review(tmp_path, fake_claude):
    """产出时 HEAD 移动（替身落盘后 commit）→ 打印 STALE 且 stale=true。"""
    repo = _make_scratch(tmp_path)
    _add_work_branch(repo)
    review_head = _git(repo, "rev-parse", "HEAD").strip()
    bindir, stdin_path = fake_claude

    r = _run_wrapper(repo, bindir, stdin_path, "work", fake_mode="movelate")

    assert r.returncode == 0, r.stdout + r.stderr
    assert "STALE" in r.stdout, r.stdout + r.stderr
    out = repo / "reviews" / f"review-{review_head}.json"
    data = json.loads(out.read_text())
    assert data["stale"] is True, "HEAD 移动后报告必须标 stale"
    assert data["review_sha"] == review_head


def test_wrapper_fresh_when_head_unchanged(tmp_path, fake_claude):
    repo = _make_scratch(tmp_path)
    _add_work_branch(repo)
    review_head = _git(repo, "rev-parse", "HEAD").strip()
    bindir, stdin_path = fake_claude

    r = _run_wrapper(repo, bindir, stdin_path, "work")

    assert r.returncode == 0, r.stdout + r.stderr
    assert "STALE" not in r.stdout
    data = json.loads((repo / "reviews" / f"review-{review_head}.json").read_text())
    assert data["stale"] is False


def test_wrapper_rejects_finding_missing_field(tmp_path, fake_claude):
    """findings 条目缺现有六字段之一（此处缺 evidence/suggestion/reproduction）必须报错。"""
    repo = _make_scratch(tmp_path)
    _add_work_branch(repo)
    bindir, stdin_path = fake_claude

    r = _run_wrapper(repo, bindir, stdin_path, "work", fake_mode="missingfield")

    assert r.returncode != 0
    assert "校验失败" in r.stderr


def test_wrapper_rejects_finding_null_field(tmp_path, fake_claude):
    """findings 字段存在但值为 null 必须报错（存在性检查不足，值也要非 None）。"""
    repo = _make_scratch(tmp_path)
    _add_work_branch(repo)
    bindir, stdin_path = fake_claude

    r = _run_wrapper(repo, bindir, stdin_path, "work", fake_mode="nullfield")

    assert r.returncode != 0
    assert "校验失败" in r.stderr
