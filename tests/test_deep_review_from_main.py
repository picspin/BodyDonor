"""deep_review_from_main.sh 确定性行为测试。

在临时 scratch git 仓库里真实跑脚本（claude 用测试替身替换），验证：
1. main 与工作树规则文件不同时，脚本提取注入的是 main 版；
2. main 上没有规则文件时脚本报错退出，不静默回退到工作树版；
3. review_sha 抓取正确、findings JSON 落盘合法。
端到端真审查（真 claude -p）不进 pytest，由轮次报告的验证记录覆盖。
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "deep_review_from_main.sh"

MAIN_RULES = "# Deep Reviewer (main)\n\n4. 测试 MAIN-MARKER: 新增行为必须有测试覆盖。\n"
WORK_RULES = "# Deep Reviewer (worktree)\n\n4. 测试 WORK-MARKER: 无需测试覆盖。\n"


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    )
    return out.stdout


def _make_scratch(tmp_path: Path, with_main_rules: bool) -> Path:
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


def _add_work_branch(repo: Path) -> None:
    _git(repo, "checkout", "-b", "work")
    (repo / ".claude" / "agents" / "deep-reviewer.md").write_text(WORK_RULES)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "worktree-relaxed")


@pytest.fixture()
def fake_claude(tmp_path: Path):
    """PATH 替身：模拟 claude -p，存档 prompt；FAKE_MODE 控制落盘行为。"""
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
        '  badjson) printf \'not json\' > "reviews/review-$sha.json" ;;\n'
        '  badstruct) printf \'{"review_sha":"WRONG","findings":1}\''
        ' > "reviews/review-$sha.json" ;;\n'
        '  *) printf \'{"review_sha":"%s","findings":[]}\' "$sha"'
        ' > "reviews/review-$sha.json" ;;\n'
        "esac\n"
    )
    fake.chmod(0o755)
    return bindir, stdin_path


def _run_script(
    repo: Path, bindir: Path, stdin_path: Path, *args: str, fake_mode: str = "write"
):
    env = os.environ.copy()
    # 防止宿主环境的 git 变量把 scratch 仓操作重定向到外部仓库
    for var in ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_OBJECT_DIRECTORY"):
        env.pop(var, None)
    env["PATH"] = f"{bindir}:{env['PATH']}"
    env["FAKE_STDIN"] = str(stdin_path)
    env["FAKE_MODE"] = fake_mode
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )


def test_extracts_main_version_not_worktree(tmp_path, fake_claude):
    repo = _make_scratch(tmp_path, with_main_rules=True)
    _add_work_branch(repo)
    bindir, stdin_path = fake_claude

    r = _run_script(repo, bindir, stdin_path)

    assert r.returncode == 0, r.stderr
    prompt = stdin_path.read_text()
    main_text = _git(repo, "show", "main:.claude/agents/deep-reviewer.md")
    assert main_text in prompt, "注入的规则必须是 main 版全文"
    assert "MAIN-MARKER" in prompt
    assert "WORK-MARKER" not in prompt, "不得注入工作树版规则"


def test_missing_main_rules_fails(tmp_path, fake_claude):
    repo = _make_scratch(tmp_path, with_main_rules=False)
    _add_work_branch(repo)  # 工作树版规则只存在于 work 分支，main 上没有
    bindir, stdin_path = fake_claude

    r = _run_script(repo, bindir, stdin_path)

    assert r.returncode != 0
    assert ".claude/agents/deep-reviewer.md" in r.stderr
    assert not stdin_path.exists(), "不得静默回退调用 claude"


def test_review_sha_and_json_output(tmp_path, fake_claude):
    repo = _make_scratch(tmp_path, with_main_rules=True)
    _add_work_branch(repo)
    bindir, stdin_path = fake_claude

    r = _run_script(repo, bindir, stdin_path)

    assert r.returncode == 0, r.stderr
    head = _git(repo, "rev-parse", "HEAD").strip()
    assert f"review_sha={head}" in r.stdout
    out = repo / "reviews" / f"review-{head}.json"
    assert out.exists(), r.stdout + r.stderr
    data = json.loads(out.read_text())
    assert data["review_sha"] == head
    assert data["findings"] == []


def test_no_stale_findings_reuse(tmp_path, fake_claude):
    """同 HEAD 残留旧 findings 文件时，claude 未写文件必须报错，不得沿用旧结果。"""
    repo = _make_scratch(tmp_path, with_main_rules=True)
    _add_work_branch(repo)
    bindir, stdin_path = fake_claude

    r1 = _run_script(repo, bindir, stdin_path)
    assert r1.returncode == 0, r1.stderr
    head = _git(repo, "rev-parse", "HEAD").strip()
    out = repo / "reviews" / f"review-{head}.json"
    assert out.exists()

    r2 = _run_script(repo, bindir, stdin_path, fake_mode="nowrite")

    assert r2.returncode != 0, r2.stdout + r2.stderr
    assert "未生成或为空" in r2.stderr
    assert not out.exists(), "脚本先删残留且不自己产出 findings"


def test_bad_json_fails(tmp_path, fake_claude):
    repo = _make_scratch(tmp_path, with_main_rules=True)
    _add_work_branch(repo)
    bindir, stdin_path = fake_claude

    r = _run_script(repo, bindir, stdin_path, fake_mode="badjson")

    assert r.returncode != 0
    assert "校验失败" in r.stderr


def test_bad_structure_fails(tmp_path, fake_claude):
    """sha 不匹配 / findings 非数组的结构无效文件必须报错，不只是 json.load 语法。"""
    repo = _make_scratch(tmp_path, with_main_rules=True)
    _add_work_branch(repo)
    bindir, stdin_path = fake_claude

    r = _run_script(repo, bindir, stdin_path, fake_mode="badstruct")

    assert r.returncode != 0
    head = _git(repo, "rev-parse", "HEAD").strip()
    assert f"review_sha={head}" in r.stderr or "review_sha" in r.stderr


def _add_origin_remote(repo: Path, rule_text: str) -> None:
    """给 scratch 仓造一个 origin/main 指针，规则文件内容由 rule_text 决定。"""
    env = os.environ.copy()
    for var in ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_OBJECT_DIRECTORY"):
        env.pop(var, None)

    def _run(*args: str, input_: str = "") -> str:
        return subprocess.run(
            ["git", *args],
            cwd=repo,
            input=input_,
            capture_output=True,
            text=True,
            env=env,
            check=True,
        ).stdout.strip()

    blob = _run("hash-object", "-w", "--stdin", input_=rule_text)
    inner = _run("mktree", input_=f"100644 blob {blob}\tdeep-reviewer.md\n")
    agents = _run("mktree", input_=f"040000 tree {inner}\tagents\n")
    root = _run("mktree", input_=f"040000 tree {agents}\t.claude\n")
    commit = _run("commit-tree", root, "-m", "origin rules", "-p", "HEAD")
    _git(repo, "update-ref", "refs/remotes/origin/main", commit)


def test_origin_rule_divergence_fails(tmp_path, fake_claude):
    """origin/main 上规则文件与本地 main 有差异时，脚本必须拒绝执行。"""
    repo = _make_scratch(tmp_path, with_main_rules=True)
    _add_work_branch(repo)
    _add_origin_remote(repo, WORK_RULES)
    bindir, stdin_path = fake_claude

    r = _run_script(repo, bindir, stdin_path)

    assert r.returncode != 0
    assert "有差异" in r.stderr
    assert not stdin_path.exists(), "规则来源分歧时不得调用 claude"


def test_origin_diverged_same_rules_warns_only(tmp_path, fake_claude):
    """origin/main 提交不同但规则文件相同时，仅 WARNING、正常产出。"""
    repo = _make_scratch(tmp_path, with_main_rules=True)
    _add_work_branch(repo)
    _add_origin_remote(repo, MAIN_RULES)
    bindir, stdin_path = fake_claude

    r = _run_script(repo, bindir, stdin_path)

    assert r.returncode == 0, r.stderr
    assert "WARNING" in r.stderr
    head = _git(repo, "rev-parse", "HEAD").strip()
    assert (repo / "reviews" / f"review-{head}.json").exists()
