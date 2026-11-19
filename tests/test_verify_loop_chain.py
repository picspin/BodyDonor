"""verify-loop 链路断言测试（M7 verify-loop skill Phase B）。

断言函数逐项测（链路脚本 chain_steps.py 的 STEP 断言本体）——不起服务、
不做真实推理（秒级；真实 CPU 分段 87s 属链路运行时，Phase C dogfood 跑）。

先红后绿记录（DESIGN.md Phase B 任务 3）：
- git_clean 断言：真红——测试先造一个白名单外脏文件再断言（红），删除后断言
  （绿）；再验白名单路径本身不误报。
- traces 两档断言：真红——空库（无 trace 行）断言必红；seed 两档后绿。
- mask_version 递增断言：真红——空库无 MV 行断言红；seed v1 后红（<2）；
  seed v2 后绿。
- export_seg 体素差=0：纯接线复核（export_and_verify 内含 verify_seg，既有
  test_export_seg.py/test_sop_class_uid.py 已真红覆盖）——此处只测断言函数
  在 mask 缺失时红，不作行为红测试（理由记录，不造假红）。

测试全部用 tmp_path scratch（不碰真实 config/、bodydonor.db——M6-F 硬门）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SKILL_SCRIPTS = (
    Path(__file__).resolve().parents[1] / ".claude" / "skills" / "verify-loop" / "scripts"
)
sys.path.insert(0, str(SKILL_SCRIPTS))

import chain_steps  # noqa: E402


class TestGitClean:
    """Step 12 git-status-clean 断言（白名单 + 计脏规则，DESIGN.md 决定 3）。"""

    def test_whitelist_paths_exempt(self) -> None:
        """白名单路径不误报（_git_exempt 单元）。"""
        assert chain_steps._git_exempt("bodydonor.db")
        assert chain_steps._git_exempt("bodydonor.db-wal")
        assert chain_steps._git_exempt("bodydonor.db-shm")
        assert chain_steps._git_exempt("eval/holdout_results.json")
        assert chain_steps._git_exempt("tests/data/x.dcm")
        assert chain_steps._git_exempt("logs/x.log")
        assert chain_steps._git_exempt("src/bodydonor/__pycache__/x.cpython-311.pyc")

    def test_nonwhitelist_counts_dirty(self) -> None:
        """白名单外路径计脏（链路泄漏判定）。"""
        assert not chain_steps._git_exempt("src/bodydonor/app.py")
        assert not chain_steps._git_exempt("new_leaked_file.txt")
        assert not chain_steps._git_exempt("config/settings.yaml")

    def test_step12_red_on_leaked_file(self, tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
        """先红：白名单外新文件在仓内 → step12_git_clean 断言失败。

        git -C tmp_path 造一个假仓（git init + 白名单外文件），monkeypatch
        REPO 指向它——真实仓 status 不动。
        """
        import subprocess

        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        leaked = tmp_path / "leaked_by_chain.txt"
        leaked.write_text("chain leak", encoding="utf-8")

        monkeypatch.setattr(chain_steps, "REPO", tmp_path)
        with pytest.raises(AssertionError, match="白名单外脏"):
            chain_steps.step12_git_clean()

    def test_step12_green_on_clean_repo(self, tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
        """后绿：仅白名单路径脏 → 断言通过。"""
        import subprocess

        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        (tmp_path / "bodydonor.db").write_bytes(b"x")
        (tmp_path / "logs").mkdir()
        (tmp_path / "logs" / "x.log").write_text("log", encoding="utf-8")

        monkeypatch.setattr(chain_steps, "REPO", tmp_path)
        out = chain_steps.step12_git_clean()
        assert out["exempt_ok"] is True


class TestMaskVersionAssertion:
    """Step 8 MaskVersion 落库断言（version 递增）。"""

    @pytest.fixture
    def scratch_db(self, tmp_path: Path, monkeypatch):  # noqa: ANN001
        """临时库 + chain_steps._settings 绑定（不动真库）。"""
        from bodydonor import db as bdb
        from bodydonor.config import Settings

        url = f"sqlite:///{tmp_path / 'mv.db'}"
        bdb.init_db(url)
        s = Settings()
        s.database.url = url

        import chain_steps as cs

        monkeypatch.setattr(cs, "_settings", lambda: s)
        return s

    def _seed_mv(self, s, series_id: str, version: int) -> None:  # noqa: ANN001
        from sqlalchemy.orm import Session

        from bodydonor import db as bdb
        from bodydonor.models import Case, MaskVersion, Series

        engine = bdb._engine(s.database.url)
        try:
            with engine.connect() as conn:
                sess = Session(conn)
                if version == 1:
                    sess.add(Case(case_id="case-vl"))
                    sess.add(Series(series_id=series_id, case_id="case-vl"))
                sess.add(
                    MaskVersion(
                        mask_version_id=f"mv-vl-{version}",
                        series_id=series_id,
                        version=version,
                        source="teacher" if version == 1 else "manual",
                        storage_key=f"case-vl/{series_id}/mask.nii.gz",
                    )
                )
                sess.commit()
                sess.close()
        finally:
            engine.dispose()

    def test_red_empty_db(self, scratch_db) -> None:  # noqa: ANN001
        """先红：空库无 MV 行 → 断言失败。"""
        with pytest.raises(AssertionError, match="MaskVersion 无行"):
            chain_steps.step8_mask_version("series-vl")

    def test_red_v1_only(self, scratch_db) -> None:  # noqa: ANN001
        """红：只有 teacher v1（save 未落 v2）→ 递增断言失败。"""
        self._seed_mv(scratch_db, "series-vl", 1)
        with pytest.raises(AssertionError, match="未递增"):
            chain_steps.step8_mask_version("series-vl")

    def test_green_v2(self, scratch_db) -> None:  # noqa: ANN001
        """后绿：teacher v1 + save v2 → 通过。"""
        self._seed_mv(scratch_db, "series-vl", 1)
        self._seed_mv(scratch_db, "series-vl", 2)
        out = chain_steps.step8_mask_version("series-vl")
        assert out["versions"] == [1, 2]
        assert out["sources"] == ["teacher", "manual"]


class TestTraceAssertion:
    """Step 11 DecisionTrace 两档断言（pipeline_stage，DESIGN.md 决定 4）。"""

    @pytest.fixture
    def scratch_db(self, tmp_path: Path, monkeypatch):  # noqa: ANN001
        from bodydonor import db as bdb
        from bodydonor.config import Settings

        url = f"sqlite:///{tmp_path / 'trace.db'}"
        bdb.init_db(url)
        s = Settings()
        s.database.url = url

        import chain_steps as cs

        monkeypatch.setattr(cs, "_settings", lambda: s)
        return s

    def _seed_trace(self, s, series_id: str, stage: str) -> None:  # noqa: ANN001
        from sqlalchemy.orm import Session

        from bodydonor import db as bdb
        from bodydonor.models import DecisionTrace

        engine = bdb._engine(s.database.url)
        try:
            with engine.connect() as conn:
                sess = Session(conn)
                sess.add(
                    DecisionTrace(
                        trace_id=f"dt-vl-{stage}",
                        series_id=series_id,
                        mask_version_id="mv-vl",
                        pipeline_stage=stage,
                    )
                )
                sess.commit()
                sess.close()
        finally:
            engine.dispose()

    def test_red_empty(self, scratch_db) -> None:  # noqa: ANN001
        """先红：空库无 trace → 两档断言失败。"""
        with pytest.raises(AssertionError, match="qc_scoring trace 缺"):
            chain_steps.step11_traces("series-vl")

    def test_red_one_stage_only(self, scratch_db) -> None:  # noqa: ANN001
        """红：只有 qc_scoring（gate_decision 缺）→ 断言失败。"""
        self._seed_trace(scratch_db, "series-vl", "qc_scoring")
        with pytest.raises(AssertionError, match="gate_decision trace 缺"):
            chain_steps.step11_traces("series-vl")

    def test_green_both_stages(self, scratch_db) -> None:  # noqa: ANN001
        """后绿：两档各 ≥1 → 通过。"""
        self._seed_trace(scratch_db, "series-vl", "qc_scoring")
        self._seed_trace(scratch_db, "series-vl", "gate_decision")
        out = chain_steps.step11_traces("series-vl")
        assert out["stages"]["qc_scoring"] == 1
        assert out["stages"]["gate_decision"] == 1

    def test_other_series_not_counted(self, scratch_db) -> None:  # noqa: ANN001
        """按 series_id 过滤：他 series 的 trace 不计入（整表计数不冒充）。"""
        self._seed_trace(scratch_db, "other-series", "qc_scoring")
        self._seed_trace(scratch_db, "other-series", "gate_decision")
        with pytest.raises(AssertionError, match="qc_scoring trace 缺"):
            chain_steps.step11_traces("series-vl")


class TestExportSegAssertion:
    """Step 9/10 SEG 导出 + 体素差=0。

    纯接线复核（export_and_verify 内含 verify_seg；diff 非零真红已由
    tests/test_export_seg.py TestVerify::test_tampered 覆盖）——此处只测
    断言函数在 mask 缺失时红（接线失败可见），不造假行为红。
    """

    def test_red_mask_missing(self, tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
        """红：mask 文件缺失 → 断言失败（接线断点可见）。"""
        from sqlalchemy.orm import Session

        from bodydonor import db as bdb
        from bodydonor.config import Settings
        from bodydonor.models import Case, Series

        url = f"sqlite:///{tmp_path / 'seg.db'}"
        bdb.init_db(url)
        s = Settings()
        s.database.url = url
        s.data_dir = tmp_path / "data"

        engine = bdb._engine(url)
        try:
            with engine.connect() as conn:
                sess = Session(conn)
                sess.add(Case(case_id="case-vl"))
                sess.add(
                    Series(
                        series_id="series-vl",
                        case_id="case-vl",
                        modality="CT",
                        storage_key="case-vl/series-vl/image.nii.gz",
                        source_instances_json={"instances": []},
                    )
                )
                sess.commit()
                sess.close()
        finally:
            engine.dispose()

        import chain_steps as cs

        monkeypatch.setattr(cs, "_settings", lambda: s)
        with pytest.raises(AssertionError, match="无 source_instances_json|mask 缺失"):
            chain_steps.step9_10_export_seg("series-vl")
