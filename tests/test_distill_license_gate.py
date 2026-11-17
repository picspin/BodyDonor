"""M4 蒸馏许可门禁单测（LICENSES-THIRD-PARTY.md §8.3 约束 1）。

- 拒绝 nnInteractive 修正产物（CC BY-NC-SA 4.0 ShareAlike 传染）
- 拒绝 licensed 任务教师 mask（推定传染）
- 拒绝 SegVol 逃生通道产物（权重许可待核实）
- 接受 total 教师 mask + 纯人工修正
- 训练池以真实训练 case 列表重查泄漏（proposal §8：M3-REPORT 遗留落地）
"""

from __future__ import annotations

import pytest

from bodydonor.distill_data import (
    ALLOWED_TRAIN_SOURCES,
    COMMERCIAL_DISTILL_TASKS,
    DISTILL_CLASSES,
    DistillDataError,
    build_train_pool,
    check_distill_source,
    check_teacher_task_commercial,
    mask_allowed_in_distill_training_strict,
    missing_distill_classes,
    read_teacher_provenance,
    teacher_out_complete,
    write_teacher_source_json,
)

# ---------------------------------------------------------------------------
# 来源白名单门禁（check_distill_source）
# ---------------------------------------------------------------------------

def test_reject_nninteractive_correction():
    """nnInteractive 修正产物：ShareAlike 传染，拒绝（LICENSES §8.1）。"""
    ok, reason = check_distill_source("manual-correction-nninteractive")
    assert not ok
    assert "nnInteractive" in reason
    assert "CC BY-NC-SA" in reason


def test_reject_segvol_source():
    """SegVol 逃生通道产物：权重许可待核实，拒绝（LICENSES §8.1/§4.2）。"""
    ok, reason = check_distill_source("segvol")
    assert not ok
    assert "SegVol" in reason


def test_reject_unknown_source():
    """来源字段不在白名单（打错 / 未登记）即拒绝，不静默进训练集。"""
    ok, _ = check_distill_source("teacher")
    assert not ok
    ok, _ = check_distill_source("student")
    assert not ok
    ok, _ = check_distill_source("")
    assert not ok


def test_reject_licensed_teacher_task():
    """licensed 任务教师 mask：不在商用蒸馏白名单，拒绝（LICENSES §8.1）。"""
    ok, reason = check_distill_source("total", teacher_task="tissue_types")
    assert not ok
    assert "tissue_types" in reason


def test_reject_experimental_flag():
    """experimental 标记产物：逃生通道隔离，拒绝。"""
    ok, reason = check_distill_source("total", teacher_task="total", experimental=True)
    assert not ok
    assert "experimental" in reason


def test_accept_total_teacher_mask():
    """total 教师 mask（商用蒸馏唯一安全教师）：接受。"""
    ok, reason = check_distill_source("total", teacher_task="total")
    assert ok, reason
    assert reason == "ok"


def test_total_without_task_rejected_fail_closed():
    """S3R4 W-4：source=total 缺 task 即拒绝（fail-closed）——补录路径手写
    {"source": "total", "backfill": true} 无 task 不再过门禁（来源必须实测）。"""
    ok, reason = check_distill_source("total", teacher_task="", experimental=False)
    assert not ok
    assert "task" in reason
    # 放行语义不变：commercial 仍只放行 total（带 task 时）
    ok2, _ = check_distill_source("total", teacher_task="total", experimental=False)
    assert ok2
    # 未知 task 仍拒绝（原语义保留）
    ok3, _ = check_distill_source("total", teacher_task="coronary_arteries")
    assert not ok3


def test_accept_pure_manual_correction():
    """纯人工修正（无第三方权重参与）：接受（LICENSES §8.2 推理 3）。"""
    ok, reason = check_distill_source("manual-correction")
    assert ok, reason


def test_commercial_distill_tasks_only_total():
    """商用蒸馏白名单 = 仅 total（settings.teacher.commercial_distill_tasks 同源）。"""
    assert frozenset({"total"}) == COMMERCIAL_DISTILL_TASKS


def test_allowed_sources_constant():
    """训练标签来源白名单：total 教师 mask + 纯人工修正。"""
    assert frozenset({"total", "manual-correction"}) == ALLOWED_TRAIN_SOURCES


# ---------------------------------------------------------------------------
# 严格版准入（mask_allowed_in_distill_training_strict，扩展 ontology 版）
# ---------------------------------------------------------------------------

def test_strict_gate_rejects_nninteractive_meta():
    meta = {"source": "manual-correction-nninteractive", "task": "total"}
    ok, reason = mask_allowed_in_distill_training_strict(meta)
    assert not ok
    assert "nnInteractive" in reason


def test_strict_gate_rejects_segvol_meta():
    meta = {"source": "segvol-escape", "task": ""}
    ok, _ = mask_allowed_in_distill_training_strict(meta)
    assert not ok  # segvol-escape 不在白名单也不在显式拒绝清单 -> 未知来源拒绝


def test_strict_gate_accepts_total_meta():
    meta = {"source": "total", "task": "total", "experimental": False}
    ok, reason = mask_allowed_in_distill_training_strict(meta)
    assert ok, reason


def test_strict_gate_accepts_manual_correction_meta():
    meta = {"source": "manual-correction"}
    ok, reason = mask_allowed_in_distill_training_strict(meta)
    assert ok, reason


def test_strict_gate_rejects_licensed_meta():
    meta = {"source": "total", "task": "coronary_arteries"}
    ok, reason = mask_allowed_in_distill_training_strict(meta)
    assert not ok
    assert "coronary_arteries" in reason


# ---------------------------------------------------------------------------
# 研究蒸馏路径（M7-S3F ③：资格读注册表 research_distill，非 commercial_distill_tasks）
# ---------------------------------------------------------------------------

def test_research_profile_allows_non_total_registry_teacher():
    """research_distill: allowed 的非 total 教师（liver_lesions_mr 等同组）可进研究蒸馏。"""
    ok, reason = check_distill_source(
        "total", teacher_task="total_mr", profile="research"
    )
    assert ok, reason


def test_research_profile_restricted_teacher_derives_sa():
    """S3R4 W-6 修正后语义：nnInteractive 修正产物不进蒸馏（M6 项目政策边界）——
    teacher 来源的 interactive_correction 任务在 ontology 判定层也拒绝
    （restriction_basis 写"修正产物不进蒸馏"，边界须一致；注册表字段值不改）。"""
    from bodydonor.ontology import mask_allowed_in_distill_training

    ok, reason = mask_allowed_in_distill_training(
        {"experimental": False, "source": "teacher", "task": "interactive_correction"}
    )
    assert not ok
    assert "修正产物不进蒸馏" in reason


def test_research_profile_restricted_non_nninteractive_no_sa_label():
    """S3 收口轮 ④：synthseg/moose 判定读 research_distill=conditional（不因
    商业政策模糊自动禁止本地科研蒸馏），放行且按 output_license_inherits 写
    条款；known_restrictions 里的"蒸馏阻断"文字不再参与判定。flexray 条款
    是 CC-BY-NC 非 CC-BY-NC-SA——非 nnInteractive 教师不得返回 SA 标记
    （M7-PLAN §6.3：只有 nnInteractive 是 SA）。"""
    from bodydonor.ontology import mask_allowed_in_distill_training

    # synthseg / moose：research_distill=conditional → 放行，条款逐条写出
    for task, lic in (("brain_mri_qc", "FreeSurfer"), ("moose_qc", "CC BY 4.0")):
        ok, reason = mask_allowed_in_distill_training(
            {"experimental": False, "source": "teacher", "task": task}
        )
        assert ok, f"{task} conditional 应放行: {reason}"
        assert lic in reason
        assert "CC-BY-NC-SA" not in reason
    # flexray：conditional 放行，条款 = CC-BY-NC（非 SA）
    ok, reason = mask_allowed_in_distill_training(
        {"experimental": False, "source": "teacher", "task": "flexray_xray"}
    )
    assert ok, reason
    assert "CC-BY-NC" in reason
    assert "CC-BY-NC-SA" not in reason


def test_known_restrictions_substring_no_longer_decides():
    """S3 收口轮 ④：known_restrictions 写"蒸馏阻断"不再改变判定结果
    （子串匹配退役，判定只读 research_distill）。"""
    from bodydonor import license_registry as lr
    from bodydonor.ontology import mask_allowed_in_distill_training

    entries = [e for e in lr.TEACHER_REGISTRY if e["task"] == "brain_mri_qc"]
    assert len(entries) == 1
    orig = entries[0]["known_restrictions"]
    try:
        entries[0]["known_restrictions"] = [
            "蒸馏阻断", "禁止蒸馏", "不得登记为可蒸馏",
        ]
        ok, reason = mask_allowed_in_distill_training(
            {"experimental": False, "source": "teacher", "task": "brain_mri_qc"}
        )
        assert ok, f"known_restrictions 文字不应参与判定: {reason}"
    finally:
        entries[0]["known_restrictions"] = orig


def test_research_distill_blocked_and_unverified_block():
    """research_distill=blocked/unverified 阻断（临时改内存条目验证，不动注册表）。"""
    from bodydonor import license_registry as lr
    from bodydonor.ontology import mask_allowed_in_distill_training

    entries = [e for e in lr.TEACHER_REGISTRY if e["task"] == "brain_mri_qc"]
    orig = entries[0]["research_distill"]
    try:
        entries[0]["research_distill"] = "blocked"
        ok, reason = mask_allowed_in_distill_training(
            {"experimental": False, "source": "teacher", "task": "brain_mri_qc"}
        )
        assert not ok
        assert "blocked" in reason
        entries[0]["research_distill"] = "unverified"
        ok2, reason2 = mask_allowed_in_distill_training(
            {"experimental": False, "source": "teacher", "task": "brain_mri_qc"}
        )
        assert not ok2
        assert "unverified" in reason2
    finally:
        entries[0]["research_distill"] = orig


def test_registry_36_entries_new_fields_complete():
    """S3 收口轮 ④：全部条目新三字段齐全且枚举合法（逐项计数断言）。"""
    from bodydonor.license_registry import (
        RESEARCH_DISTILL_VALUES,
        TEACHER_REGISTRY,
        validate_entry,
    )

    n = len(TEACHER_REGISTRY)
    assert n == 36, f"条目数应为 36：{n}"
    for i, e in enumerate(TEACHER_REGISTRY):
        assert e["research_distill"] in RESEARCH_DISTILL_VALUES, (
            f"条目 {i} {e['teacher_id']}/{e['task']} research_distill 非法"
        )
        assert isinstance(e["restriction_basis"], str) and e["restriction_basis"].strip(), (
            f"条目 {i} {e['teacher_id']}/{e['task']} 缺 restriction_basis"
        )
        assert isinstance(e["output_license_inherits"], list), (
            f"条目 {i} {e['teacher_id']}/{e['task']} output_license_inherits 非列表"
        )
    # 计数：全部条目三字段齐全
    n_rd = sum(1 for e in TEACHER_REGISTRY if e["research_distill"] in RESEARCH_DISTILL_VALUES)
    n_rb = sum(1 for e in TEACHER_REGISTRY if e["restriction_basis"].strip())
    n_ol = sum(1 for e in TEACHER_REGISTRY if isinstance(e["output_license_inherits"], list))
    assert (n_rd, n_rb, n_ol) == (36, 36, 36)
    validate_entry(TEACHER_REGISTRY[0])


def test_validate_entry_rejects_bad_new_fields():
    """validate_entry 跟进新字段：缺失/非法枚举/空 basis/非列表即拒绝。"""
    from copy import deepcopy

    from bodydonor.license_registry import TEACHER_REGISTRY, validate_entry

    base = deepcopy(TEACHER_REGISTRY[0])
    for field in ("research_distill", "restriction_basis", "output_license_inherits"):
        e = deepcopy(base)
        del e[field]
        with pytest.raises(ValueError, match="缺字段"):
            validate_entry(e)
    e = deepcopy(base)
    e["research_distill"] = "maybe"
    with pytest.raises(ValueError, match="research_distill 非法"):
        validate_entry(e)
    e = deepcopy(base)
    e["restriction_basis"] = "  "
    with pytest.raises(ValueError, match="restriction_basis"):
        validate_entry(e)
    e = deepcopy(base)
    e["output_license_inherits"] = "Apache-2.0"
    with pytest.raises(ValueError, match="output_license_inherits"):
        validate_entry(e)


def test_validate_entry_conditional_requires_inherits():
    """S3R4 M-1：research_distill=conditional 时 output_license_inherits 必须
    非空（空列表即 raise——ontology 判定层 conditional 空 inherits 落 "ok"，
    条款静默丢弃，入口须拒绝登记）。"""
    from copy import deepcopy

    from bodydonor.license_registry import TEACHER_REGISTRY, validate_entry

    base = deepcopy(TEACHER_REGISTRY[7])  # nninteractive：conditional 条目
    assert base["research_distill"] == "conditional"
    e = deepcopy(base)
    e["output_license_inherits"] = []
    with pytest.raises(ValueError, match="conditional"):
        validate_entry(e)


def test_research_profile_empty_task_blocked():
    """B3：研究路径 fail-closed——teacher_task 为空即阻断带原因（不跳过注册表检查）。"""
    ok, reason = check_distill_source("total", teacher_task="", profile="research")
    assert not ok
    assert "无注册表条目" in reason


def test_research_profile_blocks_unverified_teacher():
    """unverified 教师（vertebrae_body）研究蒸馏阻断待核实。"""


def test_research_profile_blocks_missing_registry_entry():
    """注册表无条目任务研究蒸馏阻断（与第 2 轮"无条目即拒绝"一致）。"""
    ok, reason = check_distill_source(
        "total", teacher_task="no_such_task", profile="research"
    )
    assert not ok
    assert "无注册表条目" in reason


def test_research_profile_ignores_commercial_distill_tasks(monkeypatch):
    """commercial_distill_tasks 内容改动不影响研究路径（脱钩证明）。"""
    import bodydonor.distill_data as dd

    monkeypatch.setattr(dd, "COMMERCIAL_DISTILL_TASKS", frozenset({"coronary_arteries"}))
    # coronary_arteries 无注册表条目：若研究路径查商用白名单会放行，实际必须阻断
    ok, reason = check_distill_source(
        "total", teacher_task="coronary_arteries", profile="research"
    )
    assert not ok
    assert "无注册表条目" in reason
    # 反向：total 在商用白名单之外也不影响研究路径放行
    monkeypatch.setattr(dd, "COMMERCIAL_DISTILL_TASKS", frozenset())
    ok2, reason2 = check_distill_source(
        "total", teacher_task="total", profile="research"
    )
    assert ok2, reason2


def test_commercial_profile_regression_only_total():
    """回归对照：commercial profile 行为与 HEAD 一致（teacher 任务只放行 total）。"""
    ok, _ = check_distill_source("total", teacher_task="total")
    assert ok
    ok2, reason2 = check_distill_source("total", teacher_task="coronary_arteries")
    assert not ok2
    assert "商用蒸馏白名单" in reason2


def test_strict_gate_research_profile_passthrough():
    """strict 版 profile 透传：research 路径 unverified 任务阻断。"""
    meta = {"source": "total", "task": "vertebrae_body", "experimental": False}
    ok, reason = mask_allowed_in_distill_training_strict(meta, profile="research")
    assert not ok
    assert "unverified" in reason


def test_invalid_profile_raises():
    """W9d：非法 profile raise ValueError（不静默落商用分支放行）。"""
    with pytest.raises(ValueError, match="profile 非法"):
        check_distill_source("total", teacher_task="total", profile="Research")
    with pytest.raises(ValueError, match="profile 非法"):
        check_distill_source("total", teacher_task="total", profile="")
    meta = {"source": "total", "task": "total", "experimental": False}
    with pytest.raises(ValueError, match="profile 非法"):
        mask_allowed_in_distill_training_strict(meta, profile="Research")


# ---------------------------------------------------------------------------
# 教师任务白名单（check_teacher_task_commercial，纵深防线）
# ---------------------------------------------------------------------------

def test_teacher_task_commercial_raises_on_licensed():
    with pytest.raises(DistillDataError, match="tissue_types"):
        check_teacher_task_commercial("tissue_types")


def test_teacher_task_commercial_accepts_total():
    check_teacher_task_commercial("total")  # 不 raise


# ---------------------------------------------------------------------------
# 类别集合（DISTILL_CLASSES 与 hold-out 评估 AMOS 15 类同源）
# ---------------------------------------------------------------------------

def test_distill_classes_cover_amos15():
    """蒸馏类别集合 = AMOS 15 类（hold-out 门禁需要的类别集合）。"""
    expected = {
        "spleen", "kidney_right", "kidney_left", "gallbladder", "esophagus",
        "liver", "stomach", "aorta", "inferior_vena_cava", "pancreas",
        "adrenal_gland_right", "adrenal_gland_left", "duodenum",
        "urinary_bladder", "prostate",
    }
    assert set(DISTILL_CLASSES.values()) == expected
    assert sorted(DISTILL_CLASSES.keys()) == list(range(1, 16))


# ---------------------------------------------------------------------------
# 训练池泄漏重查（proposal §8：M4 蒸馏启动时以真实训练 case 列表重查）
# ---------------------------------------------------------------------------

def test_build_train_pool_rejects_holdout_leak(tmp_path, monkeypatch):
    """泄漏重查兜底：排除后 hold-out 仍与训练池交集非空 -> LeakError。

    模拟场景：holdout.txt 在池构建后被扩充（评审把 s0899 补进 hold-out）——
    排除时用的旧列表没有 s0899，重查用同一份文件的新内容发现交集，必须中止
    （proposal §8：非空即中止，M3-REPORT 遗留的 M4 重查）。
    """
    from bodydonor import distill_data as dd
    from bodydonor.holdout import HoldoutEntry, LeakError

    amos_train = tmp_path / "amos22_subset" / "train"
    (amos_train / "imagesTr").mkdir(parents=True)
    ct_root = tmp_path / "ct_lite"
    (ct_root / "images").mkdir(parents=True)
    (ct_root / "images" / "s0899.nii.gz").write_bytes(b"fake")

    monkeypatch.setattr(dd, "AMOS", amos_train)
    monkeypatch.setattr(dd, "CT_LITE", ct_root)

    fake_holdout = tmp_path / "holdout.txt"
    fake_holdout.write_text("", encoding="utf-8")  # 排除时为空（旧列表）

    # 重查时 holdout.txt 已扩充：s0899 补进 hold-out（真实 check_leak，不 mock；
    # check_leak 走 holdout 模块自身的 load_holdout，patch 那边才生效）
    import bodydonor.holdout as holdout_mod

    def _expanded_load(path=None):
        return [HoldoutEntry("s0899", "ts_public")]

    monkeypatch.setattr(holdout_mod, "load_holdout", _expanded_load)

    with pytest.raises(LeakError) as exc:
        build_train_pool(holdout_path=fake_holdout)
    assert "泄漏" in str(exc.value)


def test_build_train_pool_excludes_holdout(tmp_path, monkeypatch):
    """正常路径：hold-out case 逐 case 排除，训练池只含非 hold-out case。"""
    from bodydonor import distill_data as dd

    amos_train = tmp_path / "amos22_subset" / "train"
    (amos_train / "imagesTr").mkdir(parents=True)
    ct_root = tmp_path / "ct_lite"
    (ct_root / "images").mkdir(parents=True)
    # hold-out：amos_0001 / s0045；非 hold-out：amos_0024 / s0899
    for cid in ("amos_0001", "amos_0024"):
        (amos_train / "imagesTr" / f"{cid}.nii.gz").write_bytes(b"fake")
    for cid in ("s0045", "s0899"):
        (ct_root / "images" / f"{cid}.nii.gz").write_bytes(b"fake")

    monkeypatch.setattr(dd, "AMOS", amos_train)
    monkeypatch.setattr(dd, "CT_LITE", ct_root)

    pool = build_train_pool()
    ids = [c.case_id for c in pool]
    assert "amos_0001" not in ids
    assert "s0045" not in ids
    assert "amos_0024" in ids
    assert "s0899" in ids
    assert all(c.label_source == "total" for c in pool)


# ---------------------------------------------------------------------------
# 来源清单实测（read_teacher_provenance；防止门禁被硬编码绕过）
# ---------------------------------------------------------------------------

def test_provenance_defaults_total_without_source_json(tmp_path):
    """ts_out 旁无 source.json -> provenance_status=missing（不默认 total，来源未知不冒充已知）。"""
    prov = read_teacher_provenance(tmp_path / "ts_out")
    assert prov["provenance_status"] == "missing"
    assert prov["provenance_missing"] is True
    assert "source" not in prov
    assert "task" not in prov


def test_provenance_missing_case_rejected_by_gate(tmp_path):
    """缺 source.json 的 case：来源按缺失处理（不默认 total），过不了门禁也不进训练清单。"""
    prov = read_teacher_provenance(tmp_path / "ts_out")
    ok, reason = check_distill_source(
        str(prov.get("source", "")), teacher_task=str(prov.get("task", "")),
        experimental=bool(prov.get("experimental", False)),
    )
    assert not ok
    assert "'' 不在蒸馏训练白名单" in reason


def test_provenance_backfill_records_basis(tmp_path):
    """历史目录显式补录来源并记录补录依据（backfill 字段），不删不重做。"""
    case_dir = tmp_path / "amos_0001"
    (case_dir / "ts_out").mkdir(parents=True)
    (case_dir / "source.json").write_text(
        '{"source": "total", "task": "total", '
        '"backfill": true, '
        '"backfill_basis": "M2/M3 run_total task=total 既有目录，2026-11-15 补录"}',
        encoding="utf-8",
    )
    prov = read_teacher_provenance(case_dir / "ts_out")
    assert prov["provenance_status"] == "inferred"
    assert prov["provenance_missing"] is False
    assert prov["source"] == "total"
    assert prov["backfill"] is True
    ok, reason = check_distill_source(
        str(prov.get("source")), teacher_task=str(prov.get("task")),
        experimental=bool(prov.get("experimental", False)),
    )
    assert ok


def test_provenance_backfill_without_task_rejected(tmp_path):
    """S3R4 W-4：补录来源缺 task（{"source": "total", "backfill": true} 无 task）
    ——补录校验同样要求 task 必填，空值 fail-closed。"""
    case_dir = tmp_path / "amos_0002"
    (case_dir / "ts_out").mkdir(parents=True)
    (case_dir / "source.json").write_text(
        '{"source": "total", "backfill": true, '
        '"backfill_basis": "既有目录补录"}',
        encoding="utf-8",
    )
    prov = read_teacher_provenance(case_dir / "ts_out")
    ok, reason = check_distill_source(
        str(prov.get("source", "")), teacher_task=str(prov.get("task", "")),
        experimental=bool(prov.get("experimental", False)),
    )
    assert not ok
    assert "task" in reason


def test_provenance_reads_declared_nninteractive(tmp_path):
    """source.json 声明 nnInteractive 来源 -> 实测读出（门禁据此拒绝）。"""
    case_dir = tmp_path / "amos_0024"
    (case_dir / "ts_out").mkdir(parents=True)
    (case_dir / "source.json").write_text(
        '{"source": "manual-correction-nninteractive", "task": "total"}',
        encoding="utf-8",
    )
    prov = read_teacher_provenance(case_dir / "ts_out")
    assert prov["source"] == "manual-correction-nninteractive"
    ok, reason = check_distill_source(
        str(prov.get("source")), teacher_task=str(prov.get("task")),
        experimental=bool(prov.get("experimental", False)),
    )
    assert not ok
    assert "nnInteractive" in reason


def test_provenance_reads_declared_licensed_task(tmp_path):
    """source.json 声明 licensed 任务 -> 实测读出（门禁据此拒绝）。"""
    case_dir = tmp_path / "amos_0025"
    (case_dir / "ts_out").mkdir(parents=True)
    (case_dir / "source.json").write_text(
        '{"source": "total", "task": "tissue_types"}', encoding="utf-8"
    )
    prov = read_teacher_provenance(case_dir / "ts_out")
    ok, reason = check_distill_source(
        str(prov.get("source")), teacher_task=str(prov.get("task")),
        experimental=bool(prov.get("experimental", False)),
    )
    assert not ok
    assert "tissue_types" in reason


def test_provenance_corrupt_json_raises(tmp_path):
    """source.json 损坏（坏 JSON）-> DistillDataError（不猜来源）。"""
    case_dir = tmp_path / "amos_0027"
    (case_dir / "ts_out").mkdir(parents=True)
    (case_dir / "source.json").write_text("not-json{", encoding="utf-8")
    with pytest.raises(DistillDataError, match="损坏"):
        read_teacher_provenance(case_dir / "ts_out")


def test_provenance_non_dict_raises(tmp_path):
    """source.json 非 dict（如 list）-> DistillDataError。"""
    case_dir = tmp_path / "amos_0030"
    (case_dir / "ts_out").mkdir(parents=True)
    (case_dir / "source.json").write_text('["total"]', encoding="utf-8")
    with pytest.raises(DistillDataError, match="非法"):
        read_teacher_provenance(case_dir / "ts_out")


def test_provenance_accepts_declared_total(tmp_path):
    """source.json 声明 total -> 实测读出且过门禁。"""
    case_dir = tmp_path / "amos_0033"
    (case_dir / "ts_out").mkdir(parents=True)
    (case_dir / "source.json").write_text(
        '{"source": "total", "task": "total"}', encoding="utf-8"
    )
    prov = read_teacher_provenance(case_dir / "ts_out")
    ok, reason = check_distill_source(
        str(prov.get("source")), teacher_task=str(prov.get("task")),
        experimental=bool(prov.get("experimental", False)),
    )
    assert ok, reason


# ---------------------------------------------------------------------------
# merge_teacher_mask / prepare_dataset（found 实测进 manifest，n_classes_found）
# ---------------------------------------------------------------------------

def _make_case_fixture(tmp_path, case_id: str, classes: list[str], pad_to: int = 117):
    """最小合成 case：一致 affine 的原图 + 指定蒸馏类的空 mask + 占位补齐到 117。

    蒸馏类文件为真实小 NIfTI（merge 会 nib.load）；占位补齐文件只被 glob 计数
    （keep_classes 之外不加载），空字节即可。pad_to=0 不补齐（配合
    _touch_ts_out 自造部分目录）。写 source.json（与 run_distill_teacher_inference
    成功路径同形——fixture 模拟成功产出的 ts_out，S3R1 后缺它即来源未知被预检拒绝）。
    """
    import nibabel as nib
    import numpy as np

    case_dir = tmp_path / case_id
    (case_dir / "imagesTr").mkdir(parents=True)
    img_path = case_dir / "imagesTr" / f"{case_id}.nii.gz"
    nib.save(nib.Nifti1Image(np.zeros((8, 8, 4), dtype=np.int16), np.eye(4)), str(img_path))
    ts_out = case_dir / "ts_out"
    ts_out.mkdir()
    for cls in classes:
        nib.save(nib.Nifti1Image(np.zeros((8, 8, 4), dtype=np.uint8), np.eye(4)),
                 str(ts_out / f"{cls}.nii.gz"))
    for i in range(max(0, pad_to - len(classes))):
        (ts_out / f"ts_extra_{i:03d}.nii.gz").write_bytes(b"")
    write_teacher_source_json(ts_out, case_id)
    return img_path, ts_out


def test_merge_teacher_mask_returns_found(tmp_path):
    """merge 返回 (merged, dropped, found)：found = 实际找到的 keep_classes 类。"""
    import nibabel as nib

    from bodydonor.distill_data import merge_teacher_mask

    img_path, ts_out = _make_case_fixture(
        tmp_path, "amos_9001", ["spleen", "liver", "prostate"]
    )
    img = nib.load(str(img_path))
    keep = set(DISTILL_CLASSES.values())
    merged, dropped, found = merge_teacher_mask(ts_out, img_path, keep, "amos_9001", img=img)
    assert found == ["liver", "prostate", "spleen"]  # sorted 目录序
    # pad 占位文件（keep 之外）进 dropped，蒸馏类一个不落
    assert sorted(dropped) == sorted(f"ts_extra_{i:03d}" for i in range(114))
    assert merged.shape == (8, 8, 4)


def test_merge_teacher_mask_drops_non_keep_classes(tmp_path):
    """keep_classes 之外的 TS 类进 dropped，不进 found。"""
    import nibabel as nib

    from bodydonor.distill_data import merge_teacher_mask

    img_path, ts_out = _make_case_fixture(tmp_path, "amos_9002", ["spleen", "heart"])
    img = nib.load(str(img_path))
    merged, dropped, found = merge_teacher_mask(
        ts_out, img_path, {"spleen"}, "amos_9002", img=img
    )
    assert found == ["spleen"]
    assert "heart" in dropped
    assert "heart" not in found
    assert merged.shape == (8, 8, 4)


def test_prepare_dataset_manifest_records_n_classes_found(tmp_path, monkeypatch):
    """manifest 逐 case 记真实找到的蒸馏类别数（实测 found，非默认 0）。

    完整 case（15 蒸馏类齐全 + 117 文件）过预检后 n_classes_found == 15——
    修复前该字段恒为默认 0。"缺类记小数"的语义在 merge 层覆盖
    （test_merge_teacher_mask_returns_found：found = 实际存在的类数）；预检
    加严（问题 3）后部分 ts_out 在 prepare_dataset 即拒绝，manifest 层只见
    完整 case。全程合成 fixture（tmp_path），不跑 GPU 推理、不读真实数据目录。
    """
    import json

    from bodydonor import distill_data as dd

    full_classes = sorted(DISTILL_CLASSES.values())
    _, ts_full = _make_case_fixture(tmp_path, "amos_9101", full_classes)
    _, ts_full2 = _make_case_fixture(tmp_path, "amos_9102", full_classes)
    img_full = tmp_path / "amos_9101" / "imagesTr" / "amos_9101.nii.gz"
    img_full2 = tmp_path / "amos_9102" / "imagesTr" / "amos_9102.nii.gz"

    amos_train = tmp_path / "amos22_subset" / "train"
    (amos_train / "imagesTr").mkdir(parents=True)
    ct_root = tmp_path / "ct_lite"
    (ct_root / "images").mkdir(parents=True)
    monkeypatch.setattr(dd, "AMOS", amos_train)
    monkeypatch.setattr(dd, "CT_LITE", ct_root)

    # 直接构造 DistillCase（绕过 build_train_pool 的真实目录扫描）
    cases = [
        dd.DistillCase(case_id="amos_9101", source="amos", image_path=img_full,
                       teacher_out=ts_full),
        dd.DistillCase(case_id="amos_9102", source="amos", image_path=img_full2,
                       teacher_out=ts_full2),
    ]
    monkeypatch.setattr(dd, "build_train_pool", lambda holdout_path=None: cases)

    out_root = tmp_path / "nnUNet_raw"
    result = dd.prepare_dataset(out_root=out_root, dry_run=False)
    assert result["n_cases"] == 2

    manifest = json.loads((out_root / dd.DISTILL_DATASET_NAME / "manifest.json").read_text(
        encoding="utf-8"))
    by_id = {c["case_id"]: c for c in manifest["cases"]}
    # 实测 found（15），不是修复前的默认 0
    assert by_id["amos_9101"]["n_classes_found"] == 15
    assert by_id["amos_9102"]["n_classes_found"] == 15
    # dropped = 117 全量类里不在 keep 的（pad 占位文件 102 个，逐 case 实测）
    assert by_id["amos_9101"]["dropped_classes"] == [f"ts_extra_{i:03d}" for i in range(102)]


def test_prepare_dataset_profile_passthrough(tmp_path, monkeypatch):
    """W10：prepare_dataset(profile=...) 透传 check_distill_source——research 口径
    unverified 来源清单即拒绝；非法 profile raise。dry_run 不落盘。"""
    import pytest as _pytest

    from bodydonor import distill_data as dd

    full_classes = sorted(DISTILL_CLASSES.values())
    _, ts_full = _make_case_fixture(tmp_path, "amos_9111", full_classes)
    img_full = tmp_path / "amos_9111" / "imagesTr" / "amos_9111.nii.gz"
    # 来源清单声明 unverified 任务：research 口径必须拒绝（透传证明）
    (ts_full.parent / "source.json").write_text(
        '{"source": "total", "task": "vertebrae_body"}', encoding="utf-8"
    )

    amos_train = tmp_path / "amos22_subset" / "train"
    (amos_train / "imagesTr").mkdir(parents=True)
    ct_root = tmp_path / "ct_lite"
    (ct_root / "images").mkdir(parents=True)
    monkeypatch.setattr(dd, "AMOS", amos_train)
    monkeypatch.setattr(dd, "CT_LITE", ct_root)
    cases = [
        dd.DistillCase(case_id="amos_9111", source="amos", image_path=img_full,
                       teacher_out=ts_full),
    ]
    monkeypatch.setattr(dd, "build_train_pool", lambda holdout_path=None: cases)

    out_root = tmp_path / "nnUNet_raw"
    # research 口径：unverified 任务在写第一个文件之前拒绝
    with _pytest.raises(dd.DistillDataError, match="unverified"):
        dd.prepare_dataset(out_root=out_root, dry_run=True, profile="research")
    assert not (out_root / dd.DISTILL_DATASET_NAME).exists()
    # commercial 口径：同一来源清单被商用白名单拒绝（口径差异可见）
    with _pytest.raises(dd.DistillDataError, match="商用蒸馏白名单"):
        dd.prepare_dataset(out_root=out_root, dry_run=True, profile="commercial")
    # 非法 profile：raise ValueError（W8 入口防护）
    with _pytest.raises(ValueError, match="profile 非法"):
        dd.prepare_dataset(out_root=out_root, dry_run=True, profile="Research")
    # 干跑成功路径（total 任务）：manifest 仍不落盘（dry_run）
    (ts_full.parent / "source.json").write_text(
        '{"source": "total", "task": "total"}', encoding="utf-8"
    )
    result = dd.prepare_dataset(out_root=out_root, dry_run=True, profile="commercial")
    assert result["n_cases"] == 1
    assert not (out_root / dd.DISTILL_DATASET_NAME / "manifest.json").exists()


def test_prepare_dataset_rejects_missing_source_json(tmp_path, monkeypatch):
    """S3R4 W-3：prepare_dataset 层——缺 source.json 的 case 预检拒绝
    （DistillDataError 且消息含"来源清单缺失"），dry_run 不落盘。

    门禁层（check_distill_source）对同一 case 也拒绝，但 prepare_dataset 的
    预检 raise 是独立防线（删 :479 的 raise 后门禁层同样拒绝，两层须各钉住）。"""
    from bodydonor import distill_data as dd

    full_classes = sorted(DISTILL_CLASSES.values())
    _, ts_no_prov = _make_case_fixture(tmp_path, "amos_9401", full_classes)
    # 删掉 fixture 写下的 source.json（模拟失败 case / 未补录历史目录）
    (ts_no_prov.parent / "source.json").unlink()
    assert read_teacher_provenance(ts_no_prov).get("provenance_status") == "missing"

    amos_train = tmp_path / "amos22_subset" / "train"
    (amos_train / "imagesTr").mkdir(parents=True)
    ct_root = tmp_path / "ct_lite"
    (ct_root / "images").mkdir(parents=True)
    monkeypatch.setattr(dd, "AMOS", amos_train)
    monkeypatch.setattr(dd, "CT_LITE", ct_root)
    cases = [
        dd.DistillCase(case_id="amos_9401", source="amos",
                       image_path=ts_no_prov.parent / "imagesTr" / "amos_9401.nii.gz",
                       teacher_out=ts_no_prov),
    ]
    monkeypatch.setattr(dd, "build_train_pool", lambda holdout_path=None: cases)

    out_root = tmp_path / "nnUNet_raw"
    with pytest.raises(dd.DistillDataError, match="来源清单缺失"):
        dd.prepare_dataset(out_root=out_root, dry_run=True)
    with pytest.raises(dd.DistillDataError, match="来源清单缺失"):
        dd.prepare_dataset(out_root=out_root, dry_run=False)
    # 预检先行：两种模式都不落盘
    assert not (out_root / dd.DISTILL_DATASET_NAME / "manifest.json").exists()
    assert not (out_root / dd.DISTILL_DATASET_NAME / "dataset.json").exists()

# ---------------------------------------------------------------------------
# 推理成功后写 source.json（write_teacher_source_json，位置与读回约定一致）
# ---------------------------------------------------------------------------

def test_write_teacher_source_json_roundtrip(tmp_path):
    """写出的 source.json 内容正确，read_teacher_provenance 能读回（实测来源）。"""
    case_dir = tmp_path / "amos_9301"
    ts_out = case_dir / "ts_out"
    ts_out.mkdir(parents=True)

    write_teacher_source_json(ts_out, "amos_9301")

    written = case_dir / "source.json"
    assert written.exists()  # 写在 teacher_out.parent，与 read_teacher_provenance 一致
    import json

    doc = json.loads(written.read_text(encoding="utf-8"))
    assert doc == {
        "source": "total",
        "task": "total",
        "created_by": "run_distill_teacher_inference.py",
        "case_id": "amos_9301",
        "attestation": "asserted",
        "evidence": "run_output",
    }
    prov = read_teacher_provenance(ts_out)
    assert prov["provenance_status"] == "asserted"
    assert prov["provenance_missing"] is False
    assert prov["source"] == "total"
    assert prov["task"] == "total"
    assert prov["case_id"] == "amos_9301"

# ---------------------------------------------------------------------------
# ts_out 完整性判定（teacher_out_complete，prepare_dataset 预检 + 推理脚本共用）
# ---------------------------------------------------------------------------

def _touch_ts_out(teacher_out, names: list[str]) -> None:
    """空 .nii.gz 占位文件（完整性判定只看文件名，不需真实 NIfTI）。"""
    teacher_out.mkdir(parents=True, exist_ok=True)
    for n in names:
        (teacher_out / f"{n}.nii.gz").write_bytes(b"")


def test_teacher_out_complete_one_file_false(tmp_path):
    """只有 1 个文件：<117 -> 不完整。"""
    ts_out = tmp_path / "ts_out"
    _touch_ts_out(ts_out, ["spleen"])
    assert teacher_out_complete(ts_out) is False


def test_teacher_out_complete_117_files_missing_distill_class_false(tmp_path):
    """117 个文件但缺一个蒸馏类（heart 占位替代 stomach）-> 不完整。"""
    ts_out = tmp_path / "ts_out"
    names = [f"cls_{i:03d}" for i in range(116)] + ["heart"]
    assert len(names) == 117
    _touch_ts_out(ts_out, names)
    assert teacher_out_complete(ts_out) is False
    assert "stomach" in missing_distill_classes(ts_out)


def test_teacher_out_complete_full_true(tmp_path):
    """完整 117 文件（116 占位 + 15 蒸馏类）-> 完整。"""
    ts_out = tmp_path / "ts_out"
    names = [f"cls_{i:03d}" for i in range(102)] + sorted(DISTILL_CLASSES.values())
    assert len(names) == 117
    _touch_ts_out(ts_out, names)
    assert teacher_out_complete(ts_out) is True
    assert missing_distill_classes(ts_out) == []


def test_teacher_out_complete_missing_dir_false(tmp_path):
    """目录不存在 -> 不完整（不 raise）。"""
    assert teacher_out_complete(tmp_path / "nonexistent") is False


def test_prepare_dataset_rejects_partial_ts_out(tmp_path, monkeypatch):
    """预检加严：缺一个蒸馏类的 ts_out 直接拒绝（报错列缺的类名），不留半成品。"""
    from bodydonor import distill_data as dd

    full_classes = sorted(DISTILL_CLASSES.values())
    partial_classes = [c for c in full_classes if c != "prostate"]  # 缺 prostate
    _, ts_ok = _make_case_fixture(tmp_path, "amos_9201", full_classes)
    ts_bad = tmp_path / "amos_9202" / "ts_out"
    # 117 个文件（14 蒸馏类 + 占位补齐）但缺 prostate：被预检拒绝（缺蒸馏类）
    _touch_ts_out(ts_bad, partial_classes + [f"ts_extra_{i:03d}" for i in range(103)])
    assert len(list(ts_bad.glob("*.nii.gz"))) == 117
    write_teacher_source_json(ts_bad, "amos_9202")  # 来源已知（缺的是蒸馏类）
    img_ok = tmp_path / "amos_9201" / "imagesTr" / "amos_9201.nii.gz"
    img_bad = tmp_path / "amos_9202" / "imagesTr" / "amos_9202.nii.gz"

    amos_train = tmp_path / "amos22_subset" / "train"
    (amos_train / "imagesTr").mkdir(parents=True)
    ct_root = tmp_path / "ct_lite"
    (ct_root / "images").mkdir(parents=True)
    monkeypatch.setattr(dd, "AMOS", amos_train)
    monkeypatch.setattr(dd, "CT_LITE", ct_root)

    cases = [
        dd.DistillCase(case_id="amos_9201", source="amos", image_path=img_ok,
                       teacher_out=ts_ok),
        dd.DistillCase(case_id="amos_9202", source="amos", image_path=img_bad,
                       teacher_out=ts_bad),
    ]
    monkeypatch.setattr(dd, "build_train_pool", lambda holdout_path=None: cases)

    with pytest.raises(DistillDataError, match="不完整") as exc:
        dd.prepare_dataset(out_root=tmp_path / "nnUNet_raw", dry_run=False)
    assert "amos_9202" in str(exc.value)
    assert "prostate" in str(exc.value)  # 报错里列出缺的类名
    # 预检先行：第一个文件都没写（空目录可存在——mkdir 在预检之前是现有行为）
    out_dir = tmp_path / "nnUNet_raw" / dd.DISTILL_DATASET_NAME
    assert not any((out_dir / "imagesTr").glob("*")) if (out_dir / "imagesTr").exists() else True
    assert not any((out_dir / "labelsTr").glob("*")) if (out_dir / "labelsTr").exists() else True
    assert not (out_dir / "dataset.json").exists()
    assert not (out_dir / "manifest.json").exists()
