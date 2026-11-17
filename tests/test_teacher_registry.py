"""教师注册表与路由拒绝测试（M7-S3 做法 5 号项）。

- 注册表字段完整性：mask 教师缺 class_map_ref / data_contract_version 拒绝登记；
  feature 教师缺 feature_schema 拒绝登记。
- 就绪三分（M7-S3P2 ⑥）：ready 布尔退役，拆 weights_present / class_map_present /
  share_alike——推导规则边界测试 + check_task 拒绝原因写明挡项（缺权重 /
  缺 class_map / SA 义务）+ 36 条目逐条三字段有值。
- check_task 读注册表：total 套到 MR 被路由拒绝并带原因；未就绪任务拒绝；
  blocked 仍一票否决；白名单回退已退役（M7-S3F ②）——注册表无条目即拒绝。
- 回退语义：planned/actual、跳过写原因、结构未覆盖 vs 输出缺失区分。

纯合成 fixture，不依赖本地产物，CI 可跑。
"""

from __future__ import annotations

import pytest

from bodydonor.config import Settings, TeacherConfig
from bodydonor.license_registry import (
    TEACHER_REGISTRY,
    FallbackRecord,
    LicenseRegistry,
    class_map_present,
    classify_missing_output,
    validate_entry,
    weights_present,
)
from bodydonor.segmentation import SegmentWorkerError, check_worker_task


def _settings(allowed: list[str] | None = None, blocked: list[str] | None = None) -> Settings:
    return Settings(
        teacher=TeacherConfig(
            allowed_tasks=allowed if allowed is not None else ["total", "total_mr"],
            blocked_tasks=blocked or ["registry_blocked_task"],
        )
    )


# --- 注册表条目自校验（模块加载时已跑一遍；这里显式断言全部合法）---


def test_all_entries_pass_validation() -> None:
    assert len(TEACHER_REGISTRY) == 36
    seen = {(e["teacher_id"], e["task"]) for e in TEACHER_REGISTRY}
    assert len(seen) == 36  # (teacher_id, task) 唯一
    for e in TEACHER_REGISTRY:
        validate_entry(e)  # 不 raise 即通过


def test_mask_entry_missing_class_map_ref_rejected() -> None:
    with pytest.raises(ValueError, match="class_map_ref"):
        validate_entry(
            {
                "teacher_id": "x",
                "task": "t",
                "checkpoint_id": "c",
                "modalities": ("CT",),
                "output_type": "mask",
                "known_restrictions": [],
                "provenance": {"source": "s", "verified": "v"},
                "weight_integrity": {},
                "attribution_required": True,
                "share_alike": False,
                "license_verified": True,
                "restriction_basis": "s",
                "output_license_inherits": [],
            }
        )


def test_mask_entry_missing_data_contract_version_rejected() -> None:
    e = {
        "teacher_id": "x",
        "task": "t",
        "checkpoint_id": "c",
        "modalities": ("CT",),
        "output_type": "mask",
        "known_restrictions": [],
        "provenance": {"source": "s", "verified": "v"},
        "weight_integrity": {},
        "attribution_required": True,
        "share_alike": False,
        "license_verified": True,
        "restriction_basis": "s",
        "output_license_inherits": [],
        "class_map_ref": "m",
    }
    with pytest.raises(ValueError, match="data_contract_version"):
        validate_entry(e)


def test_feature_entry_missing_feature_schema_rejected() -> None:
    with pytest.raises(ValueError, match="feature_schema"):
        validate_entry(
            {
                "teacher_id": "x",
                "task": "t",
                "checkpoint_id": "c",
                "modalities": ("MR",),
                "output_type": "feature",
                "known_restrictions": [],
                "provenance": {"source": "s", "verified": "v"},
                "weight_integrity": {},
                "attribution_required": True,
                "share_alike": False,
                "license_verified": True,
                "restriction_basis": "s",
                "output_license_inherits": [],
            }
        )


def test_missing_required_field_rejected() -> None:
    e = dict(TEACHER_REGISTRY[0])
    del e["provenance"]
    with pytest.raises(ValueError, match="provenance"):
        validate_entry(e)


def test_placeholder_class_map_ref_blocked_at_routing_both_wordings() -> None:
    """W16（M7-S3P2 ⑥ 字段化迁移）：占位 class_map_ref 派生 class_map_present=
    False，路由侧拒绝派发——「待建」与「待落地」两种措辞都触发（占位类映射
    永远不能当作在位；登记带占位 ref 仍合法——13 个未就绪条目即此形态）。"""
    base = {
        "teacher_id": "x",
        "task": "t",
        "checkpoint_id": "c",
        "modalities": ("CT",),
        "output_type": "mask",
        "known_restrictions": [],
        "provenance": {"source": "s", "verified": "v"},
        "weight_integrity": {
            "sha256": "a", "size_bytes": 1, "status": "verified"
        },
        "attribution_required": True,
        "share_alike": False,
        "license_verified": True,
        "restriction_basis": "s",
        "output_license_inherits": [],
        "class_map_ref": "ts-classes/v1 (待建)",
        "data_contract_version": "v1",
    }
    validate_entry(base)  # 登记合法（不 raise）
    e2 = dict(base)
    e2["class_map_ref"] = "ts-classes/v1 (待落地：2.10.0 class_map 不含，需按 README 定义建)"
    validate_entry(e2)
    from bodydonor import license_registry as lr

    saved = lr.TEACHER_REGISTRY
    lr.TEACHER_REGISTRY = [base]
    try:
        ok, msg = LicenseRegistry(_settings()).check_task("t", modality="CT")
        assert not ok
        assert "缺 class_map" in msg, msg
    finally:
        lr.TEACHER_REGISTRY = saved
    saved = lr.TEACHER_REGISTRY
    lr.TEACHER_REGISTRY = [e2]
    try:
        ok, msg = LicenseRegistry(_settings()).check_task("t", modality="CT")
        assert not ok
        assert "缺 class_map" in msg, msg
    finally:
        lr.TEACHER_REGISTRY = saved


# --- check_task 读注册表 ---


def test_total_on_mr_routed_rejected_with_reason() -> None:
    """total 是 CT-only；套到 MR 须被路由拒绝并带模态原因（M7-S3 测试项 1）。"""
    ok, msg = LicenseRegistry(_settings()).check_task("total", modality="MR")
    assert not ok
    assert "modality 'MR' not supported" in msg


def test_worker_task_mr_rejected_via_check_worker_task() -> None:
    """S3 B1 失败测试先行：worker 分发路径（check_worker_task）带 modality
    时 CT-only 任务派 MR 被拒（生产派发链路的门控，不只函数层）。"""
    with pytest.raises(SegmentWorkerError, match="modality 'MR' not supported"):
        check_worker_task("total", _settings(), modality="MR")


def test_ready_follows_modality_filter() -> None:
    """S3 W1（M7-S3P2 ⑥ 字段化迁移）：就绪判定跟随模态过滤——同 task 双模态
    条目（CT 缺权重、MR 三字段齐）下 CT 拒绝、MR 放行。"""
    entry_ct = dict(TEACHER_REGISTRY[0])
    entry_ct["modalities"] = ("CT",)
    entry_ct["weight_integrity"] = {"sha256": None, "size_bytes": None, "status": "not_recorded"}
    entry_mr = dict(TEACHER_REGISTRY[0])
    entry_mr["modalities"] = ("MR",)
    entry_mr["weight_integrity"] = {
        "sha256": "a", "size_bytes": 1, "status": "verified"
    }
    reg = LicenseRegistry(_settings())
    from bodydonor import license_registry as lr

    saved = lr.TEACHER_REGISTRY
    lr.TEACHER_REGISTRY = [entry_ct, entry_mr]
    try:
        ok, _ = reg.check_task("total", modality="CT")
        assert not ok  # CT 条目缺权重 → 拒绝（不落到 MR 条目）
        ok, msg = reg.check_task("total", modality="MR")
        assert ok, msg
    finally:
        lr.TEACHER_REGISTRY = saved


def test_modality_normalization_case_and_whitespace() -> None:
    """S3 W8 失败测试先行：模态比较归一化——小写/带空白按大写去空白处理。"""
    ok, msg = LicenseRegistry(_settings()).check_task("total", modality=" ct ")
    assert ok, msg
    ok, msg = LicenseRegistry(_settings()).check_task("total", modality="ct")
    assert ok, msg
    # 归一化后仍不匹配的模态照拒
    ok, msg = LicenseRegistry(_settings()).check_task("total", modality="MR")
    assert not ok
    assert "modality 'MR' not supported" in msg


def test_dicom_pt_code_matches_pet_registered_modality() -> None:
    """R3 ④ 失败测试先行：DICOM Modality 代码 PT（PS3.3 C.7.3.1.1 定义词，真实
    PET series 的头内代码）须匹配注册表登记的 'PET'——ingest 层原样记录头内值，
    不翻译；check_task 归一化后 PT == PET，否则 PET series 一律被拒。"""
    from bodydonor import license_registry as lr

    nni = next(
        e for e in lr.TEACHER_REGISTRY
        if e["teacher_id"] == "nninteractive" and e["task"] == "interactive_correction"
    )
    saved = lr.TEACHER_REGISTRY
    # nninteractive 条目（PET 已登记）；share_alike=False 使就绪三分不因 SA 挡
    # （⑥：本测试只验模态匹配，SA 挡项另有测试）
    lr.TEACHER_REGISTRY = [{**nni, "share_alike": False}]
    try:
        ok, msg = LicenseRegistry(_settings()).check_task(
            "interactive_correction", modality="PT"
        )
        assert ok, msg
    finally:
        lr.TEACHER_REGISTRY = saved


def test_modality_aliases_in_registry_entries_match_ingest_values() -> None:
    """R3 ④：flexray 条目登记 'X-RAY'（比对侧 strip().upper() 后相等）——
    混合大小写 'X-ray' 永不相等（潜在缺口，ready=False 掩盖）。"""
    by_task = {e["task"]: e for e in TEACHER_REGISTRY}
    fx = by_task["flexray_xray"]["modalities"]
    assert fx == ("X-RAY",), f"flexray modalities 应为 ('X-RAY',) 实为 {fx}"
    # W2 修复落地：投影 X 光 DICOM 代码 DX/CR 与 PET 代码 PT 经别名匹配登记模态
    from bodydonor.license_registry import MODALITY_ALIASES

    assert MODALITY_ALIASES["DX"] == "X-RAY"
    assert MODALITY_ALIASES["CR"] == "X-RAY"
    assert MODALITY_ALIASES["PT"] == "PET"


def test_every_entry_modalities_nonempty_and_provenance_cited() -> None:
    """R3 ④ 审计覆盖（字面口径层）：全部条目 modalities 非空且登记值在归一化
    口径内合法，provenance 带 source（书面依据可追溯）。逐教师的具体期望值
    对照在 test_r3_modality_expectations_per_teacher——本测试只拦口径外取值。"""
    legal = {"CT", "MR", "PET", "X-RAY"}
    for e in TEACHER_REGISTRY:
        mods = e["modalities"]
        assert isinstance(mods, tuple) and mods, f"{e['teacher_id']}/{e['task']} modalities 空"
        assert set(mods) <= legal, f"{e['teacher_id']}/{e['task']} 登记值 {mods} 不在口径内"
        assert e["provenance"].get("source"), f"{e['teacher_id']}/{e['task']} 缺 provenance.source"


def test_r3_modality_expectations_per_teacher() -> None:
    """R3 ④ 审计断言：各教师 modalities 与书面依据一致（出处见注册表注释）。

    - totalsegmentator: total 族 CT（红线：不动）；_mr 族 MR（LICENSE-CHECK §四）。
    - nninteractive: CT/MR/PET（README §Abstract 训练数据集清单）。
    - nnunet_v2: CT/MR/PET（MR/PET §一 总表 'MRI/PET'；CT §七 结论）。
    - moose: CT/PET（LICENSE-CHECK §一 总表）。
    - mri_core: MR（§二）。synthseg: MR（§六）。flexray: X-RAY（§三）。
    """
    # N6：task 唯一性先行断言——by_task 字典静默覆盖会让下面的断言对象错位
    tasks = [e["task"] for e in TEACHER_REGISTRY]
    assert len(tasks) == len(set(tasks)), f"task 重复：{[t for t in tasks if tasks.count(t) > 1]}"
    by_task = {e["task"]: e for e in TEACHER_REGISTRY}
    assert by_task["total"]["modalities"] == ("CT",)  # S3 验收安全属性，不动
    assert by_task["total_mr"]["modalities"] == ("MR",)
    nni = next(
        e for e in TEACHER_REGISTRY
        if e["teacher_id"] == "nninteractive" and e["task"] == "interactive_correction"
    )
    assert nni["modalities"] == ("CT", "MR", "PET")
    assert by_task["framework"]["modalities"] == ("CT", "MR", "PET")
    assert by_task["moose_qc"]["modalities"] == ("CT", "PET")
    assert by_task["feature_encoding"]["modalities"] == ("MR",)
    assert by_task["brain_mri_qc"]["modalities"] == ("MR",)
    assert by_task["flexray_xray"]["modalities"] == ("X-RAY",)


def test_total_on_ct_allowed() -> None:
    ok, _ = LicenseRegistry(_settings()).check_task("total", modality="CT")
    assert ok


def test_mr_task_not_ready_rejected() -> None:
    """注册表 MR 任务 ready=False → 拒绝带原因（TS MR 权重未下载）。"""
    ok, msg = LicenseRegistry(_settings()).check_task("body_mr", modality="MR")
    assert not ok
    assert "not ready" in msg
    # S3R4 M-7：ready=False 拒绝消息纯技术原因——不含许可措辞
    # （R2 报告声称的断言落地：'许可'/'BLOCKED'/'license' 不在消息内）
    assert "许可" not in msg
    assert "BLOCKED" not in msg.upper()
    assert "license" not in msg.lower()


def test_lung_vessels_not_ready_message_clean() -> None:
    """S3R4 M-7：注册条目 ready=False（lung_vessels）拒绝消息同样纯技术原因。"""
    ok, msg = LicenseRegistry(_settings(allowed=[])).check_task("lung_vessels", modality="CT")
    assert not ok
    assert "not ready" in msg
    assert "许可" not in msg
    assert "BLOCKED" not in msg.upper()
    assert "license" not in msg.lower()


def test_blocked_task_still_vetoed() -> None:
    """blocked_tasks 机制：一票否决（内存条目——R3 ④删 brain_aneurysm 商用
    理由后机制保留，fixture 用无注册条目的占位任务名验证机制）。"""
    ok, msg = LicenseRegistry(_settings()).check_task("registry_blocked_task")
    assert not ok
    assert "BLOCKED" in msg


def test_unknown_task_rejected() -> None:
    ok, msg = LicenseRegistry(_settings(allowed=[])).check_task("no_such_task")
    assert not ok
    assert "registry" in msg
    # S3R4 M-4：未注册任务拒绝消息不带许可措辞（分层承诺只对 ready=False 成立，
    # 未注册消息只指登记路径，不引用 LICENSE 文档）
    assert "LICENSE" not in msg.upper()
    assert "BLOCKED" not in msg.upper()


def test_whitelist_fallback_retired() -> None:
    """M7-S3F ② 失败测试先行：白名单回退已退役——注册表无条目即拒绝带原因，
    改 allowed_tasks 内容不再影响路由结果。"""
    ok, msg = LicenseRegistry(_settings(allowed=["legacy_task"])).check_task("legacy_task")
    assert not ok
    assert "not in teacher registry" in msg
    # blocked/ready 语义不受白名单影响：blocked 一票否决保持
    ok, msg = LicenseRegistry(_settings(allowed=["registry_blocked_task"])).check_task(
        "registry_blocked_task"
    )
    assert not ok
    assert "BLOCKED" in msg


def test_allowed_tasks_registered_tasks_route_via_registry() -> None:
    """M7-S3F ② 失败测试先行（M7-S3P2 ⑥ 字段化迁移）：allowed_tasks 的分割
    任务已登记进注册表——路由走注册表（未就绪拒绝、unverified 标记、空配置
    可拒绝/可放行）。"""
    from bodydonor.license_registry import TEACHER_REGISTRY as R

    by_task = {e["task"]: e for e in R}
    # 24 个 TS 任务（组一 18 + 组二 5 + 组三 vertebrae_body 单列；LEGACY 不登记）
    for t in (
        "lung_vessels", "body", "cerebral_bleed", "hip_implant",
        "pleural_pericard_effusion", "head_glands_cavities", "head_muscles",
        "headneck_bones_vessels", "headneck_muscles", "liver_vessels",
        "oculomotor_muscles", "lung_nodules", "kidney_cysts", "breasts",
        "liver_segments", "craniofacial_structures", "abdominal_muscles",
        "ventricle_parts", "teeth", "trunk_cavities", "liver_lesions",
        "vertebrae_pp", "vertebrae_pp_refined", "vertebrae_body",
    ):
        e = by_task.get(t)
        assert e is not None, f"{t} 应已登记进注册表"
        assert e["teacher_id"] == "totalsegmentator"
        assert e["weight_integrity"]["status"] == "not_recorded"  # 权重未部署，不造假
    # open 清单双证据齐全组 → license_verified=true；仅 README 单证据组 → false
    # （M7-S3P2 ⑤：准入证据强度由 license_verified 承载，三值枚举退役）
    assert by_task["lung_vessels"]["license_verified"] is True
    assert by_task["teeth"]["license_verified"] is False
    assert by_task["vertebrae_body"]["license_verified"] is False
    assert any("commercial_models" in r for r in by_task["vertebrae_body"]["known_restrictions"])
    # 空配置（allowed_tasks=[]）下已登记任务走注册表：未就绪拒绝带原因
    ok, msg = LicenseRegistry(_settings(allowed=[])).check_task("lung_vessels", modality="CT")
    assert not ok
    assert "not ready" in msg
    # 就绪条目（total，权重实测 verified）不依赖白名单
    ok, _ = LicenseRegistry(_settings(allowed=[])).check_task("total", modality="CT")
    assert ok


# --- 回退语义（§2.2.3）---


def test_fallback_record_planned_then_skipped_with_reason() -> None:
    rec = FallbackRecord(planned={"teacher_id": "totalsegmentator", "task": "total"})
    assert rec.status == "planned"
    rec.status = "skipped"
    rec.skip_reason = "modality MR not supported"
    assert rec.actual is None
    assert rec.skip_reason == "modality MR not supported"


def test_fallback_record_planned_then_executed() -> None:
    rec = FallbackRecord(planned={"teacher_id": "totalsegmentator", "task": "total"})
    rec.status = "executed"
    rec.actual = {"teacher_id": "totalsegmentator", "task": "total"}
    assert rec.planned == {"teacher_id": "totalsegmentator", "task": "total"}
    assert rec.actual == {"teacher_id": "totalsegmentator", "task": "total"}
    assert rec.status == "executed"


def test_structure_not_covered_vs_output_missing() -> None:
    """执行成功没返回目标 mask：结构未覆盖 ≠ 输出缺失，不计成 Dice 0。"""
    assert (
        classify_missing_output(["prostate"], [], ["aorta", "liver"])
        == "structure_not_covered"
    )
    assert classify_missing_output(["liver"], [], ["aorta", "liver"]) == "output_missing"
    assert classify_missing_output([], [], ["liver"]) == "output_missing"
    assert classify_missing_output(["liver"], ["liver"], ["liver"]) == "executed"


def test_structure_not_covered_partial_overlap() -> None:
    """请求多结构、教师只覆盖其一：算覆盖（至少一个目标在教师范围内）。"""
    assert (
        classify_missing_output(["prostate", "liver"], [], ["liver"])
        == "output_missing"
    )


# --- 就绪三分（M7-S3P2 ⑥）：推导规则 + 挡项原因 + 逐条有值 ---


def test_weights_present_derivation_boundaries() -> None:
    """⑥ 失败测试先行：weights_present 推导规则边界——status=verified → True；
    not_recorded / 缺 weight_integrity → False（规则写死进 docstring，全仓统一）。"""
    e = dict(TEACHER_REGISTRY[0])
    e["weight_integrity"] = {"sha256": "a", "size_bytes": 1, "status": "verified"}
    assert weights_present(e) is True
    e["weight_integrity"] = {"sha256": None, "size_bytes": None, "status": "not_recorded"}
    assert weights_present(e) is False
    e2 = {"task": "t"}
    assert weights_present(e2) is False  # 缺 weight_integrity → False（不抛异常）


def test_class_map_present_derivation_boundaries() -> None:
    """⑥ 失败测试先行：class_map_present 推导规则边界——无占位标记 → True；
    「待建」/「待落地」→ False；缺 class_map_ref → False（feature/framework
    条目无 class_map_ref，推导不抛异常）。"""
    e = dict(TEACHER_REGISTRY[0])
    e["class_map_ref"] = "ontology/v1/map.yaml"
    assert class_map_present(e) is True
    e["class_map_ref"] = "synthseg-classes/v1 (待建)"
    assert class_map_present(e) is False
    e["class_map_ref"] = "ts-mr-classes/v1 (TS MR 50 类，待落地为 ontology 条目)"
    assert class_map_present(e) is False
    assert class_map_present({"task": "t"}) is False  # 缺 class_map_ref → False


def test_check_task_rejection_reason_names_blocked_item() -> None:
    """⑥ 失败测试先行：check_task 拒绝原因三分——原因字符串写明挡项
    （缺权重 / 缺 class_map / SA 义务），不让一个 "not ready" 承包。"""
    reg = LicenseRegistry(_settings())
    # 缺权重：total_mr（weight_integrity not_recorded）
    ok, msg = reg.check_task("body_mr", modality="MR")
    assert not ok
    assert "缺权重" in msg, msg
    # 缺 class_map：TS MR 50 类占位（weights 齐、class_map 待落地）
    e = dict(TEACHER_REGISTRY[0])
    e["task"] = "t_cm"
    e["weight_integrity"] = {"sha256": "a", "size_bytes": 1, "status": "verified"}
    e["class_map_ref"] = "ts-mr-classes/v1 (待落地为 ontology 条目)"
    from bodydonor import license_registry as lr

    saved = lr.TEACHER_REGISTRY
    lr.TEACHER_REGISTRY = [e]
    try:
        ok, msg = LicenseRegistry(_settings()).check_task("t_cm", modality="CT")
        assert not ok
        assert "缺 class_map" in msg, msg
        # SA 义务：三字段齐但 share_alike=True（nninteractive 形态）
        e2 = dict(e)
        e2["task"] = "t_sa"
        e2["class_map_ref"] = "prompt-targets/v1"
        e2["share_alike"] = True
        lr.TEACHER_REGISTRY = [e2]
        ok, msg = LicenseRegistry(_settings()).check_task("t_sa", modality="CT")
        assert not ok
        assert "SA 义务" in msg, msg
    finally:
        lr.TEACHER_REGISTRY = saved


def test_all_36_entries_three_ready_fields_valued() -> None:
    """⑥ 验收：36 条目逐条三字段可推导且有值（decision-table 式逐条断言）；
    ready 布尔在条目字典清零（字段退役，fixture 防回潮）。"""
    from bodydonor.license_registry import class_map_present as cp
    from bodydonor.license_registry import weights_present as wp

    assert len(TEACHER_REGISTRY) == 36
    for e in TEACHER_REGISTRY:
        assert isinstance(wp(e), bool), f"{e['teacher_id']}/{e['task']} weights_present 无值"
        assert isinstance(cp(e), bool), f"{e['teacher_id']}/{e['task']} class_map_present 无值"
        assert isinstance(e["share_alike"], bool), (
            f"{e['teacher_id']}/{e['task']} share_alike 无值"
        )
        assert "ready" not in e, f"{e['teacher_id']}/{e['task']} 仍有 ready 布尔"


def test_src_no_ready_residual() -> None:
    """⑥：src 全树注册表语义 ready 零残留（字段退役，防线防回潮）；
    recalibration_ready 是数值重算抽检另一概念，豁免列出。"""
    from pathlib import Path

    src_root = Path(__file__).resolve().parents[1] / "src"
    hits = []
    for p in src_root.rglob("*.py"):
        text = p.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            if "ready" not in line:
                continue
            # recalibration_ready（qc.py 数值重算抽检）与历史口径叙述豁免
            if "recalibration_ready" in line:
                continue
            if '"ready"' in line or "'ready'" in line or '["ready"]' in line:
                hits.append(f"{p}:{i}: {line.strip()}")
    assert hits == [], f"src 残留注册表语义 ready：{hits}"
