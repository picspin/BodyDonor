"""① commit-msg hook 硬隔离 config/ 的实测验证。

在临时 scratch git 仓库里装 hook 后实际跑 git commit 断言行为，
安装方式走 README.md 的同一路径（git config core.hooksPath）。
"""
import subprocess
from pathlib import Path

import pytest

HOOK_SRC = Path(__file__).resolve().parent.parent / "scripts" / "git-hooks"


def _run(cmd, cwd):
    return subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, check=False
    )


@pytest.fixture()
def scratch_repo(tmp_path):
    """临时 git 仓库，hook 按 README 安装方式（core.hooksPath）指向主仓的 hook 脚本。"""
    repo = tmp_path / "scratch"
    repo.mkdir()
    _run(["git", "init", "-q"], repo)
    _run(["git", "config", "user.email", "t@t"], repo)
    _run(["git", "config", "user.name", "t"], repo)
    _run(["git", "config", "core.hooksPath", str(HOOK_SRC)], repo)
    # 首提交建立基线
    (repo / "README.md").write_text("base\n")
    _run(["git", "add", "."], repo)
    r = _run(["git", "commit", "-q", "-m", "init"], repo)
    assert r.returncode == 0, r.stderr
    return repo


def _touch_config(repo, content="x: 1\n"):
    cfg = repo / "config" / "settings.yaml"
    cfg.parent.mkdir(exist_ok=True)
    cfg.write_text(content)
    _run(["git", "add", "config/"], repo)


def test_config_change_without_marker_rejected(scratch_repo):
    _touch_config(scratch_repo)
    r = _run(["git", "commit", "-m", "test: no marker"], scratch_repo)
    assert r.returncode != 0
    assert "[config-change]" in r.stderr
    # 提交未产生：HEAD 仍是 init
    head = _run(["git", "log", "--oneline", "-1"], scratch_repo).stdout
    assert "init" in head


def test_config_change_with_marker_allowed(scratch_repo):
    _touch_config(scratch_repo)
    r = _run(
        ["git", "commit", "-m", "test: cfg [config-change]"], scratch_repo
    )
    assert r.returncode == 0, r.stderr


def test_normal_commit_without_config_allowed(scratch_repo):
    (scratch_repo / "src.txt").write_text("hi\n")
    _run(["git", "add", "src.txt"], scratch_repo)
    r = _run(["git", "commit", "-m", "test: normal"], scratch_repo)
    assert r.returncode == 0, r.stderr


def test_config_delete_without_marker_rejected(scratch_repo):
    _touch_config(scratch_repo)
    _run(["git", "commit", "-q", "-m", "add cfg [config-change]"], scratch_repo)
    (scratch_repo / "config" / "settings.yaml").unlink()
    _run(["git", "add", "-A", "config/"], scratch_repo)
    r = _run(["git", "commit", "-m", "test: delete cfg no marker"], scratch_repo)
    assert r.returncode != 0


def test_amend_with_config_and_marker_allowed(scratch_repo):
    _touch_config(scratch_repo)
    r = _run(["git", "commit", "-m", "cfg [config-change]"], scratch_repo)
    assert r.returncode == 0, r.stderr
    (scratch_repo / "config" / "settings.yaml").write_text("y: 2\n")
    _run(["git", "add", "config/"], scratch_repo)
    r = _run(
        ["git", "commit", "--amend", "-m", "cfg amended [config-change]"],
        scratch_repo,
    )
    assert r.returncode == 0, r.stderr


def test_rename_out_of_config_without_marker_rejected(scratch_repo):
    # 审查 blocker：--name-only 默认 renames 只列新路径，rename out 逃检
    _touch_config(scratch_repo)
    _run(["git", "commit", "-q", "-m", "add cfg [config-change]"], scratch_repo)
    (scratch_repo / "config" / "settings.yaml").rename(
        scratch_repo / "settings.yaml"
    )
    _run(["git", "add", "-A"], scratch_repo)
    r = _run(["git", "commit", "-m", "rename out no marker"], scratch_repo)
    assert r.returncode != 0


def test_non_ascii_config_name_without_marker_rejected(scratch_repo):
    # 审查 W1：默认 core.quotePath=true 输出带引号转义，^config/ 不匹配
    cfg = scratch_repo / "config" / "中文.yaml"
    cfg.parent.mkdir(exist_ok=True)
    cfg.write_text("x: 1\n")
    _run(["git", "add", "config/"], scratch_repo)
    r = _run(["git", "commit", "-m", "nonascii no marker"], scratch_repo)
    assert r.returncode != 0


def test_amend_no_edit_reuses_old_marker_pinned_limitation(scratch_repo):
    # 审查 W3：--no-edit 复用旧消息标记、本次新改动放行——README 已文档化的接受限制
    _touch_config(scratch_repo)
    _run(["git", "commit", "-q", "-m", "cfg [config-change]"], scratch_repo)
    (scratch_repo / "config" / "settings.yaml").write_text("y: 2\n")
    _run(["git", "add", "config/"], scratch_repo)
    r = _run(["git", "commit", "--amend", "--no-edit"], scratch_repo)
    assert r.returncode == 0, r.stderr


def test_comment_line_marker_not_recognized(scratch_repo):
    _touch_config(scratch_repo)
    msg_file = scratch_repo / "msg.txt"
    msg_file.write_text("test: no marker\n#[config-change] in comment\n")
    r = _run(["git", "commit", "-F", str(msg_file)], scratch_repo)
    assert r.returncode != 0
    assert "[config-change]" in r.stderr


def test_hook_install_doc_matches_fixture(tmp_path):
    """fixture 安装方式与 README 文档的安装命令一致（防止文档漂移）。"""
    readme = (HOOK_SRC / "README.md").read_text()
    assert "git config core.hooksPath scripts/git-hooks" in readme
