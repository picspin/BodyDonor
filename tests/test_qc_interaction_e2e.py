"""M6-b 验收回放：真实全路径 provenance → qc_series 过程级规则（test_qc_interaction_e2e.py）。

- 真实链路：复用 TestCorrectSubmitE2E 的 correct→abandon→correct→submit
  HTTP 端到端（真实 nnInteractive CPU 推理，48³ patch ~18s/次 ×3，**不 mock
  该链路**），submit 落最新 MaskVersion 的 provenance.interactionMetrics 后
  直接调 qc_series 回放过程级规则。
- 合成边界 case：qc_series 侧补 seed（手工 provenance，标注合成）覆盖
  真实链路造不出的分支（高风险类放弃 flag、纯自动 skipped 已有单测）。
- 互补性：过程级 flag 集合 vs 结果级 QC flag 集合的重叠/互补数字（含干净
  三向对照：全 thresholds vs 全 thresholds-两过程规则关）。
- 真实 case（amos_0004 patch 源文件）缺失时 skip（与 TestCorrectSubmitE2E
  同条件），不 fail。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np
import pytest

from bodydonor.config import Settings
from bodydonor.qc import RULE_MULTI_TEACHER, load_qc_thresholds

# 过程级规则名（qc_interaction 导入）
from bodydonor.qc_interaction import (
    RULE_INTERACTION_ABANDONMENT,
    RULE_INTERACTION_CONVERGENCE,
)
from bodydonor.qc_pipeline import qc_series


def _real_patch_available() -> bool:
    """真实 amos_0004 源文件存在（TestCorrectSubmitE2E real_patch 同条件）。"""
    src = (
        Path.home()
        / "data"
        / "BodyDonor"
        / "downloads"
        / "amos22_subset"
        / "train"
        / "imagesTr"
        / "amos_0004.nii.gz"
    )
    return src.exists()


def _interaction_thresholds(min_score: float = 0.0) -> dict:
    """只开两条件过程级规则的最小 thresholds（其余规则 disabled——回放只看过程级）。

    min_score=0：过程 flag 不至于把 overall_score 压到 min_score 以下干扰
    passed 判定（互补性数字只看 flags 集合，不看 passed）。
    """
    th = load_qc_thresholds()
    rules = {}
    for name, cfg in th["rules"].items():
        if name in (RULE_INTERACTION_CONVERGENCE, RULE_INTERACTION_ABANDONMENT):
            rules[name] = dict(cfg)
        else:
            rules[name] = {"enabled": False}
    return {
        "cold_start": {"dataset": "m6b_replay"},
        "rules": rules,
        "overall": {"hard_flag_rules": [], "min_score": min_score},
    }


# ---------------------------------------------------------------------------
# 真实全路径回放（真实 nnInteractive 推理，不 mock 链路）
# ---------------------------------------------------------------------------


@pytest.mark.local_artifacts
@pytest.mark.skipif(
    not _real_patch_available(),
    reason="真实 case 缺失（amos_0004 patch 源文件不存在）",
)
class TestReplayRealPath:
    """correct→abandon→correct→submit 真实链路 → qc_series 回放过程级规则。"""

    @pytest.fixture(scope="class")
    def real_patch(self) -> Path:
        """真实 amos_0004 裁 48³ patch（与 TestCorrectSubmitE2E 同源）。"""
        import nibabel as nib

        src = (
            Path.home()
            / "data"
            / "BodyDonor"
            / "downloads"
            / "amos22_subset"
            / "train"
            / "imagesTr"
            / "amos_0004.nii.gz"
        )
        img = nib.load(str(src))
        arr = np.asarray(img.dataobj)
        patch = arr[290:338, 231:279, 30:78]
        patch_img = nib.Nifti1Image(np.ascontiguousarray(patch, dtype=np.float32), img.affine)
        import tempfile

        out = Path(tempfile.mkdtemp(prefix="m6b-replay-")) / "patch48.nii.gz"
        nib.save(patch_img, str(out))
        return out

    @pytest.fixture
    def replay_series(self, request, tmp_path, settings, monkeypatch):  # noqa: ANN001
        """真实 patch 入库（storage+DB 隔离到 tmp）→ (sid, storage, settings)。

        与 TestCorrectSubmitE2E.e2e_series 同构（tracker 清空注册为 finalizer；
        settings.database.url 在 scan 前指 tmp——series_id = hash(文件名)）。
        另 data_dir 指 tmp：qc_series 读 settings.resolved_data_dir / "storage"
        （与 app.storage 的 monkeypatch 无关），不指则 submit 的 mask_vN 落
        tmp storage 而 qc_series 读真实目录 → KeyError。
        """
        import nibabel as nib
        from fastapi.testclient import TestClient  # noqa: F401

        patch = request.getfixturevalue("real_patch")

        from bodydonor import app as app_module
        from bodydonor import db
        from bodydonor.models import Series
        from bodydonor.pipeline import commit, scan
        from bodydonor.storage import LocalFsStorage

        request.addfinalizer(app_module._interaction_trackers.clear)
        tmp_url = f"sqlite:///{tmp_path}/m6b_replay.db"
        monkeypatch.setattr(
            app_module.settings,
            "database",
            type(app_module.settings.database)(url=tmp_url),
        )
        settings.database.url = tmp_url
        monkeypatch.setattr(app_module.settings, "data_dir", tmp_path / "data")
        settings.data_dir = tmp_path / "data"  # scan/commit/qc_series 同一 resolved_data_dir
        app_module._interaction_trackers.clear()

        from bodydonor.db import init_db

        init_db(tmp_url)
        outcome = scan(patch, settings, modality="CT")
        # storage 根与 qc_series 统一（settings.resolved_data_dir / "storage" =
        # tmp/data/storage）：app._get_storage 返回模块级 storage（monkeypatch
        # 本属性），qc_series 自建 LocalFsStorage(settings.resolved_data_dir /
        # "storage")——两处不同根会让 submit 的 mask_vN 落 tmp/storage 而
        # qc_series 读 tmp/data/storage → KeyError
        storage = LocalFsStorage(settings.resolved_data_dir / "storage")
        monkeypatch.setattr(app_module, "storage", storage)
        committed = commit(outcome, settings, storage)
        sid = committed.selected_series_ids[0]

        from sqlalchemy import select

        engine = db._engine(settings.database.url)
        try:
            with engine.connect() as conn:
                from sqlalchemy.orm import Session

                s = Session(conn)
                row = s.execute(select(Series).where(Series.series_id == sid)).scalar_one()
                key = row.storage_key
                s.close()
        finally:
            engine.dispose()

        # 落基线 teacher mask（前景 (18:22)³，与 TestCorrectSubmitE2E 同——
        # finalLocalDice 落在 (0,1) 真实区间）
        img = nib.load(str(storage.root / key))
        mask_arr = np.zeros(img.shape, dtype=np.uint8)
        mask_arr[18:22, 18:22, 18:22] = 1
        mask = nib.Nifti1Image(mask_arr, img.affine)
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".nii.gz", delete=False) as tf:
            tmp_name = tf.name
        try:
            nib.save(mask, tmp_name)
            payload = Path(tmp_name).read_bytes()
        finally:
            Path(tmp_name).unlink(missing_ok=True)
        storage.save(key.replace("image.nii.gz", "mask.nii.gz"), payload)
        return sid, storage, settings

    def _correct_payload(self, z: float, class_name: str) -> dict:
        """前端 review.html m5RunInference 的实际 payload 形状（含 class_name）。"""
        return {
            "prompts": [{"type": "point", "x": 24, "y": 24, "z": z, "include": True}],
            "target_label": 1,
            "class_name": class_name,
        }

    def test_replay_full_path_process_rules(self, replay_series, tmp_path: Path) -> None:  # noqa: ANN001
        """真实链路 submit → provenance.interactionMetrics → qc_series 过程级规则。

        abandonedAttempts=1（aorta 非高风险、普通类）→ interaction_abandonment
        flag（放弃事件本身是过程信号，两层同判据）；interaction_convergence
        不 flag（新序列 1 步，numPrompts=1、convergenceSteps=None）。
        结果级 QC flag（同 thresholds 全开）与过程级互补数字一并记录。
        """
        from fastapi.testclient import TestClient

        from bodydonor.app import app

        sid, storage, settings = replay_series
        semantic_name = "aorta"
        with TestClient(app) as c:
            # correct ×2（草稿 2 步序列）→ abandon → correct ×1（新序列）
            for z in (23, 24):
                r = c.post(
                    f"/review/{sid}/nninteractive-correct",
                    json=self._correct_payload(z, semantic_name),
                )
                assert r.status_code == 200, r.text
            ra = c.post(
                f"/review/{sid}/abandon-draft",
                json={"target_label": 1, "class_name": semantic_name},
            )
            assert ra.status_code == 200
            assert ra.json()["abandonedAttempts"] == 1
            r = c.post(
                f"/review/{sid}/nninteractive-correct",
                json=self._correct_payload(25, semantic_name),
            )
            assert r.status_code == 200, r.text
            rs = c.post(
                f"/review/{sid}/submit-nni",
                json={
                    "target_label": 1,
                    "prompts": [
                        {
                            "type": "point",
                            "x": 24,
                            "y": 24,
                            "z": 25,
                            "include": True,
                            "class": semantic_name,
                        }
                    ],
                    "acceptedClasses": [semantic_name],
                    "sessionId": "m6b-replay",
                    "baselineTeacherVersion": None,
                },
            )
        assert rs.status_code == 200, rs.text
        prov = rs.json()["provenance"]
        im = prov["interactionMetrics"]
        assert im != {}, "真实链路 interactionMetrics 不应落空"
        entry = im[semantic_name]
        assert entry["abandonedAttempts"] == 1
        assert entry["numPrompts"] == 1

        # ---- qc_series 回放（过程级规则，最小 thresholds）----
        th = _interaction_thresholds()
        out = qc_series(sid, settings, thresholds=th)
        flags = set(out["flags"])
        assert RULE_INTERACTION_ABANDONMENT in flags, out["rule_scores"]
        assert RULE_INTERACTION_CONVERGENCE not in flags
        ab_detail = out["rule_scores"][RULE_INTERACTION_ABANDONMENT]
        assert ab_detail["flagged_classes"] == [semantic_name]
        conv_detail = out["rule_scores"][RULE_INTERACTION_CONVERGENCE]
        assert conv_detail["flagged"] is False
        assert conv_detail["worst_num_prompts"] == 1

        # ---- 互补性：过程级 vs 结果级（同 case，全规则 thresholds）----
        out_full = qc_series(sid, settings, thresholds=load_qc_thresholds())
        result_flags = set(out_full["flags"])
        process_rule_set = {RULE_INTERACTION_CONVERGENCE, RULE_INTERACTION_ABANDONMENT}
        overlap = sorted(flags & result_flags)
        only_result = sorted(result_flags - flags)
        # 互补性可度量口径：结果级 flag 集合里既有过过程级 flag（重叠）又有
        # 过程级不知道的维度（only_result，体素/统计规则——过程信号不覆盖），
        # 且过程级 flag 的信号（abandonment 事件）在最小 thresholds 下就 flag
        # （结果级全 thresholds 下同 rule 名也 flag）——两类规则各提供对方
        # 没有的信号，非完全重叠
        n_process = len([f for f in result_flags if f in process_rule_set])
        n_result_only = len([f for f in only_result if f not in process_rule_set])
        assert n_process >= 1, f"过程级 flag 缺失：{sorted(result_flags)}"
        assert n_result_only >= 1, (
            f"无可度量互补性（结果级无过程级不覆盖的维度）："
            f"process={sorted(flags)} result={sorted(result_flags)}"
        )

        # ---- 干净三向对照（新口径）：两边都是全 thresholds，唯一变量是两
        # 过程规则开/关。内存深拷贝副本，不改 config 文件。----
        th_ro = copy.deepcopy(load_qc_thresholds())
        for name in (RULE_INTERACTION_CONVERGENCE, RULE_INTERACTION_ABANDONMENT):
            th_ro["rules"][name]["enabled"] = False
        out_ro = qc_series(sid, settings, thresholds=th_ro)
        result_only_flags = set(out_ro["flags"])
        only_process = sorted(result_flags - result_only_flags)
        # only_result（新口径）：关过程规则后仍出现的非过程规则 flag——结果
        # 级规则不依赖过程规则独立 flag。与 overlap 可重合（同一结果级 flag
        # 既被两边共享、又在过程规则关闭后独立出现），按规则类别计数
        only_result2 = sorted(f for f in result_only_flags if f not in process_rule_set)
        overlap2 = sorted(result_flags & result_only_flags)
        # 过程规则的增量贡献：关掉后丢的 flag（abandonment 事件）与仍留的
        # flag（boundary_smoothness 体素统计）——各 ≥1，两类规则非完全重叠
        assert len(only_process) >= 1, (
            f"过程规则无增量贡献（关掉后 flags 不变）：{sorted(result_flags)}"
        )
        assert RULE_INTERACTION_ABANDONMENT in only_process, (
            f"only_process 应含 abandonment：{only_process}"
        )
        assert len(only_result2) >= 1, (
            f"关过程规则后无结果级 flag 残留："
            f"full={sorted(result_flags)} ro={sorted(result_only_flags)}"
        )
        assert "boundary_smoothness" in only_result2, (
            f"only_result 应含 boundary_smoothness：{only_result2}"
        )

        summary = {
            "case": sid,
            "process_flags_min_thresholds": sorted(flags),
            "result_flags_full_thresholds": sorted(result_flags),
            "overlap": overlap,
            "only_result": only_result,
            "n_process_flagged": n_process,
            "n_result_only_flagged": n_result_only,
            # ---- 新口径：全 thresholds vs 全 thresholds-两过程规则关 ----
            "result_only_flags": sorted(result_only_flags),
            "only_process": only_process,
            "only_result_clean": only_result2,
            "overlap_clean": overlap2,
        }
        (tmp_path / "m6b_complementarity.json").write_text(
            json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8"
        )

    def test_replay_pure_auto_skipped(self, replay_series) -> None:  # noqa: ANN001
        """纯自动 case（无 interactionMetrics）：qc_series 两规则显式 skipped。

        seed 一个 teacher MaskVersion（provenance=None——segmentation worker
        落的 MV 真实形态，纯自动 case 没有 nnInteractive 交互），qc_series 取
        该 MV 回放。commit 本身不建 MaskVersion（只有 segmentation worker /
        submit 建），fixture 后 DB 无 MV，本测试 seed 之（storage_key 指向
        fixture 已落盘的 mask.nii.gz，真实像素文件）。
        """
        import nibabel as nib
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from bodydonor import db
        from bodydonor.models import MaskVersion

        sid, storage, settings = replay_series
        engine = db._engine(settings.database.url)
        try:
            with engine.connect() as conn:
                s = Session(conn)
                n_before = (
                    s.execute(
                        select(MaskVersion)
                        .where(MaskVersion.series_id == sid[:60])
                        .order_by(MaskVersion.version.desc())
                        .limit(1)
                    )
                    .scalars()
                    .first()
                )
                s.close()
        finally:
            engine.dispose()
        assert n_before is None, "fixture 后不应有 MaskVersion（commit 不建 MV）"
        # seed teacher MV（provenance=None）：mask 用 fixture 已落盘的基线 mask
        row = storage.list_prefix("patch48")
        mask_key = next(k for k in row if k.endswith("/mask.nii.gz"))
        nib.load(str(storage.root / mask_key))  # 校验可读（真实像素文件）
        engine = db._engine(settings.database.url)
        try:
            with db.write_lock, engine.connect() as conn:
                s = Session(conn)
                s.add(
                    MaskVersion(
                        mask_version_id="mv-m6b-pure-auto",
                        series_id=sid[:60],
                        version=1,
                        source="teacher",
                        storage_key=mask_key,
                        provenance=None,
                    )
                )
                s.commit()
                s.close()
            out = qc_series(sid, settings, thresholds=_interaction_thresholds())
            rs_c = out["rule_scores"][RULE_INTERACTION_CONVERGENCE]
            rs_a = out["rule_scores"][RULE_INTERACTION_ABANDONMENT]
            assert rs_c == {"skipped": "no interaction metrics"}
            assert rs_a == {"skipped": "no interaction metrics"}
            assert RULE_INTERACTION_CONVERGENCE not in out["flags"]
            assert RULE_INTERACTION_ABANDONMENT not in out["flags"]
        finally:
            engine.dispose()


# ---------------------------------------------------------------------------
# 合成边界 case（qc_series 侧 seed；标注 synthetic，只用于分支覆盖）
# ---------------------------------------------------------------------------


def _seed_with_provenance(
    tmp_path: Path,
    provenance: dict | None,
    storage_key: str = "case-m6b/series-m6b/mask.nii.gz",
) -> tuple[str, Settings]:
    """最小 seed：Case/Series/MaskVersion（provenance 由调用方给，合成标注）。

    返回 (series_id, settings)。mask 为 4³ aorta 体素（体素规则不干扰）。
    """
    import nibabel as nib
    from sqlalchemy.orm import Session

    from bodydonor import db
    from bodydonor.db import init_db
    from bodydonor.models import Case, MaskVersion, Series
    from bodydonor.storage import LocalFsStorage

    url = f"sqlite:///{tmp_path / 'm6b_synth.db'}"
    init_db(url)
    s = Settings()
    s.database.url = url
    st = LocalFsStorage(s.resolved_data_dir / "storage")
    arr = np.zeros((4, 4, 4), dtype=np.uint8)
    arr[1:3, 1:3, 1:3] = 3
    p = tmp_path / "synth_mask.nii.gz"
    nib.save(nib.Nifti1Image(arr, np.eye(4)), str(p))
    st.save(storage_key, p.read_bytes())
    st.save(
        storage_key.replace("mask.nii.gz", "labels.json"),
        json.dumps({"labels": [{"index": 3, "totalseg_class": "aorta"}]}).encode(),
    )
    engine = db._engine(url)
    try:
        with engine.connect() as c:
            sess = Session(c)
            sess.add(Case(case_id="case-m6b"))
            sess.add(Series(series_id="series-m6b", case_id="case-m6b"))
            sess.add(
                MaskVersion(
                    mask_version_id="mv-m6b",
                    series_id="series-m6b",
                    source="teacher",
                    storage_key=storage_key,
                    provenance=provenance,
                )
            )
            sess.commit()
            sess.close()
    finally:
        engine.dispose()
    return "series-m6b", s


class TestSyntheticBoundary:
    """合成边界 case（qc_series 全路径；provenance 手工构造，标注 synthetic）。"""

    def test_high_risk_abandonment_flags_via_qc_series(self, tmp_path: Path) -> None:
        """高风险类（prostate，teacherReliability=low）放弃 → abandonment flag。"""
        prov = {
            "sessionId": "synthetic-high-risk",
            "interactionMetrics": {
                "prostate": {
                    "numPrompts": 1,
                    "convergenceSteps": None,
                    "perStepDeltaDice": [],
                    "finalLocalDice": None,
                    "teacherReliability": "low",
                    "abandonedAttempts": 1,
                }
            },
        }
        sid, s = _seed_with_provenance(tmp_path, prov)
        out = qc_series(sid, s, thresholds=_interaction_thresholds())
        flags = set(out["flags"])
        assert RULE_INTERACTION_ABANDONMENT in flags
        ab = out["rule_scores"][RULE_INTERACTION_ABANDONMENT]
        assert ab["flagged_classes"] == ["prostate"]
        assert ab["high_risk_flagged"] == ["prostate"]
        # 高风险类不看 convergenceSteps：convergence 规则不 flag
        assert RULE_INTERACTION_CONVERGENCE not in flags

    def test_high_risk_convergence_rule_ignores_steps(self, tmp_path: Path) -> None:
        """高风险类 convergenceSteps=99：convergence 规则不看该维度，不 flag。"""
        prov = {
            "sessionId": "synthetic-high-risk-steps",
            "interactionMetrics": {
                "prostate": {
                    "numPrompts": 1,
                    "convergenceSteps": 99,
                    "perStepDeltaDice": [],
                    "finalLocalDice": None,
                    "teacherReliability": "low",
                    "abandonedAttempts": 0,
                }
            },
        }
        sid, s = _seed_with_provenance(tmp_path, prov)
        out = qc_series(sid, s, thresholds=_interaction_thresholds())
        assert RULE_INTERACTION_CONVERGENCE not in out["flags"]
        conv = out["rule_scores"][RULE_INTERACTION_CONVERGENCE]
        assert conv["flagged"] is False

    def test_normal_class_over_prompts_flags_via_qc_series(self, tmp_path: Path) -> None:
        """普通类 numPrompts=16（> n_max 15）→ convergence flag。"""
        prov = {
            "sessionId": "synthetic-normal-over",
            "interactionMetrics": {
                "liver": {
                    "numPrompts": 16,
                    "convergenceSteps": 1,
                    "perStepDeltaDice": [],
                    "finalLocalDice": 0.9,
                    "teacherReliability": "high",
                    "abandonedAttempts": 0,
                }
            },
        }
        sid, s = _seed_with_provenance(tmp_path, prov)
        out = qc_series(sid, s, thresholds=_interaction_thresholds())
        assert RULE_INTERACTION_CONVERGENCE in out["flags"]
        conv = out["rule_scores"][RULE_INTERACTION_CONVERGENCE]
        assert conv["flagged_classes"] == ["liver"]
        assert RULE_INTERACTION_ABANDONMENT not in out["flags"]

    def test_no_provenance_skipped(self, tmp_path: Path) -> None:
        """provenance=None（纯自动 teacher MaskVersion）：两规则 skipped。"""
        sid, s = _seed_with_provenance(tmp_path, None)
        out = qc_series(sid, s, thresholds=_interaction_thresholds())
        assert out["rule_scores"][RULE_INTERACTION_CONVERGENCE] == {
            "skipped": "no interaction metrics"
        }
        assert out["rule_scores"][RULE_INTERACTION_ABANDONMENT] == {
            "skipped": "no interaction metrics"
        }
        assert out["flags"] == []

    def test_multi_teacher_still_skipped_alongside(self, tmp_path: Path) -> None:
        """过程级规则真跑时 multi_teacher（无学生 mask）仍 skipped——互不干扰。"""
        prov = {
            "sessionId": "synthetic-mixed",
            "interactionMetrics": {
                "liver": {
                    "numPrompts": 1,
                    "convergenceSteps": None,
                    "perStepDeltaDice": [],
                    "finalLocalDice": None,
                    "teacherReliability": "high",
                    "abandonedAttempts": 0,
                }
            },
        }
        sid, s = _seed_with_provenance(tmp_path, prov)
        th = _interaction_thresholds()
        th["rules"][RULE_MULTI_TEACHER] = {"enabled": True, "threshold": 0.80}
        out = qc_series(sid, s, thresholds=th)
        assert out["rule_scores"][RULE_MULTI_TEACHER] == {"skipped": "no student mask"}
        assert RULE_INTERACTION_CONVERGENCE not in out["flags"]
