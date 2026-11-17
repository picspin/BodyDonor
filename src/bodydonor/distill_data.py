"""蒸馏数据集准备（M4，proposal §6 阶段 6 + §8 泄漏检查 + LICENSES §8.3）。

**许可门禁（LICENSES-THIRD-PARTY.md §8.3，承重件）**：

- 训练标签**永远只用** TotalSegmentator `total` 教师 mask（117 类每类一个 NIfTI，
  复用 M2/M3 已有 ts_out 目录）+ 纯人工修正（source=manual-correction）。
- **拒绝**：nnInteractive 修正产物（权重 CC BY-NC-SA 4.0 ShareAlike 传染）、
  TS 18 个 licensed 任务教师 mask（推定传染）、SegVol 逃生通道产物（权重许可
  待核实）。来源字段不在白名单即 raise，不静默跳过。
- AMOS labelsTr 人工金标准是 **hold-out 真值**，绝不进训练集；CT-Lite masks/
  （教师同源 TS 标准值编码）同理不进训练集——训练标签只有 ts_out 教师 mask。
- 每 case 来源 manifest 逐 case 可审计（供 ModelVersion 审计，§8.3 约束 1）。

**类别范围决定（实测依据，2026-11-08）**：

- 训练池仅 35 例（AMOS 25 + CT-Lite 10），TS total 117 类全量在小数据上不可行：
  频次实测（CT-Lite 10 例）最高 heart 7/10，多类 <5 例出现；AMOS 25 例需现跑
  TS。117 类全量 = 每类样本数过低 + 类不平衡极端，nnU-Net 小数据难收敛。
- **收敛到 hold-out 门禁需要的类别集合**：AMOS 15 类（spleen/kidney_right/
  kidney_left/gallbladder/esophagus/liver/stomach/aorta/inferior_vena_cava/
  pancreas/adrenal_gland_right/adrenal_gland_left/duodenum/urinary_bladder/
  prostate）——覆盖 hold-out 评估的 AMOS 15 类映射（evaluate_holdout.py
  AMOS_LABEL_MAP）；CT-Lite 真值（ts_class_map.json）含同名类，逐类 Dice 可算。
- 不静默丢类：被排除的 TS 类名与排除原因写进 manifest 的 excluded_classes。

**数据目录**：`~/data/BodyDonor/nnUNet_raw/Dataset501_BodyDonorDistill`（nnU-Net
原始数据格式：imagesTr/ labelsTr/ dataset.json），dataset 501 避开 nnU-Net
自带 001-500 示例编号。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .holdout import check_leak, load_holdout

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = Path.home() / "data" / "BodyDonor" / "downloads"
CT_LITE = DATA_ROOT / "ct_lite"
AMOS = DATA_ROOT / "amos22_subset" / "train"

# nnU-Net 原始数据目录（编号 501 避开 nnU-Net 001-500 示例）
DISTILL_DATASET_ID = 501
DISTILL_DATASET_NAME = f"Dataset{DISTILL_DATASET_ID:03d}_BodyDonorDistill"

# ---------------------------------------------------------------------------
# 许可门禁（LICENSES-THIRD-PARTY.md §8.1/§8.3；teacher 资格读注册表 research_distill）
# ---------------------------------------------------------------------------

# 训练标签允许的来源白名单：TS total 教师 mask + 纯人工修正
ALLOWED_TRAIN_SOURCES = frozenset({"total", "manual-correction"})

# 拒绝清单（逐项对应 §8.1 表）：不在 ALLOWED_TRAIN_SOURCES 即拒绝，
# 显式列出以给出精确错误信息（来源字段打错也不得静默进训练集）
# 命名 v1→v2（用户 2026-11-09 三态决定）：规范名 nninteractive_guided；
# manual-correction-nninteractive（v1，阶段 2a sidecar 用过）与 nninteractive
# 是 legacy 别名——永不再由新代码写出，但拒绝保留（删拒绝是安全倒退）。
REJECTED_SOURCES: dict[str, str] = {
    "nninteractive_guided": (
        "nnInteractive 修正产物（规范名，v2）：权重 CC BY-NC-SA 4.0 ShareAlike "
        "传染，不进商用蒸馏训练集（LICENSES §8.1/§2.2）"
    ),
    "manual-correction-nninteractive": (
        "nnInteractive 修正产物（legacy 别名 v1，永不再写出）：权重 "
        "CC BY-NC-SA 4.0 ShareAlike 传染，不进商用蒸馏训练集（LICENSES §8.1/§2.2）"
    ),
    "nninteractive": (
        "nnInteractive 修正产物（legacy 别名，永不再写出）：权重 CC BY-NC-SA 4.0 "
        "ShareAlike 传染，不进商用蒸馏训练集（LICENSES §8.1/§2.2）"
    ),
    "segvol": (
        "SegVol 逃生通道产物：BAAI/SegVol 权重许可待核实，"
        "不进训练集（LICENSES §8.1/§4.2）"
    ),
    "experimental": (
        "experimental 标记产物：逃生通道结果隔离，不进蒸馏训练集"
        "（ontology.mask_allowed_in_distill_training 同语义）"
    ),
}

def check_distill_source(
    source: str,
    teacher_task: str = "",
    experimental: bool = False,
) -> tuple[bool, str]:
    """蒸馏训练集准入检查（LICENSES §8.3 约束 1 的代码强制）。

    返回 (allowed, reason)。拒绝一律给出精确理由，不静默跳过。

    - source 必须在 ALLOWED_TRAIN_SOURCES（total 教师 mask 或纯人工修正）
    - nnInteractive / SegVol / experimental 来源显式拒绝（REJECTED_SOURCES）
    """
    if source in REJECTED_SOURCES:
        return False, REJECTED_SOURCES[source]
    if source not in ALLOWED_TRAIN_SOURCES:
        return False, (
            f"来源 '{source}' 不在蒸馏训练白名单 {sorted(ALLOWED_TRAIN_SOURCES)}"
            "（不在白名单即拒绝，LICENSES §8.3）"
        )
    if experimental:
        return False, "experimental 标记产物不进蒸馏训练集（逃生通道隔离）"
    if source == "total" and not teacher_task:
        # S3R4 W-4：source=total 缺 task 即拒绝（fail-closed）——补录路径手写
        # {"source": "total", "backfill": true} 无 task 不再过门禁（来源必须实测）
        return False, (
            "source='total' 缺 teacher_task：来源必须实测（补录须写 task；"
            "空 task 不冒充已知——fail-closed）"
        )
    if source == "total":
        # R3 ④：teacher 资格走注册表 research_distill 单口径（商用白名单常量
        # 已删，防线不倒退——无条目/blocked/unverified 即阻断）
        from .ontology import mask_allowed_in_distill_training

        return mask_allowed_in_distill_training(
            {"source": "teacher", "task": teacher_task, "experimental": False}
        )
    return True, "ok"


def mask_allowed_in_distill_training_strict(mask_meta: dict[str, Any]) -> tuple[bool, str]:
    """扩展 ontology.mask_allowed_in_distill_training 的严格版。

    ontology 版只挡 experimental + teacher 任务；本版还挡：
    - nnInteractive 修正产物（source=nninteractive_guided 规范名 v2 +
      legacy 别名 manual-correction-nninteractive / nninteractive）
    - SegVol 逃生通道产物（source=segvol / segvol-escape）
    - 未知来源（不在 ALLOWED_TRAIN_SOURCES 也不在 REJECTED_SOURCES）
    """
    if mask_meta.get("experimental"):
        return False, "experimental 标记的 mask 不进蒸馏训练集（逃生通道产物隔离）"
    return check_distill_source(
        str(mask_meta.get("source", "")),
        teacher_task=str(mask_meta.get("task", "")),
    )


class DistillDataError(Exception):
    """蒸馏数据准备失败（许可门禁拒绝、泄漏、数据缺失等）。"""


# ---------------------------------------------------------------------------
# AMOS 15 类（hold-out 门禁需要的类别集合；evaluate_holdout.AMOS_LABEL_MAP 同源）
# ---------------------------------------------------------------------------

# AMOS22 官方 15 类 -> TS total 类名（与 eval/evaluate_holdout.py AMOS_LABEL_MAP
# 同源；蒸馏类别集合收敛到这 15 类的理由见模块 docstring）
# dict 字面量 = 唯一事实源（勿改键序；值 = TS total 类名，键 = nnU-Net labelsTr 编码）
DISTILL_CLASSES: dict[int, str] = {
    1: "spleen",
    2: "kidney_right",
    3: "kidney_left",
    4: "gallbladder",
    5: "esophagus",
    6: "liver",
    7: "stomach",
    8: "aorta",
    9: "inferior_vena_cava",
    10: "pancreas",
    11: "adrenal_gland_right",
    12: "adrenal_gland_left",
    13: "duodenum",
    14: "urinary_bladder",
    15: "prostate",
}


def nnunet_label_values() -> dict[int, str]:
    """nnU-Net labelsTr 的 label 编码：1..15 = DISTILL_CLASSES（0=背景）。

    直接沿用 AMOS labelsTr 的官方 1-15 编码（hold-out 评估同源，逐类对齐
    零映射成本）。
    """
    return dict(DISTILL_CLASSES)


# ---------------------------------------------------------------------------
# 训练池构建（hold-out 泄漏检查强制前置）
# ---------------------------------------------------------------------------

@dataclass
class DistillCase:
    """一个训练 case：图像 + 教师 mask 目录 + 来源清单。"""

    case_id: str
    source: str  # amos | ct_lite（图像来源库，非标签来源）
    image_path: Path
    teacher_out: Path  # TS total 输出目录（每类一个 NIfTI）
    # S3R4 M-3：默认空串——来源未知不冒充 total；prepare_dataset 门禁通过后实测覆盖
    label_source: str = ""
    teacher_task: str = ""
    # S3P2-R2：证明层级（asserted=实测 | inferred=回填推断）——manifest 条目
    # 必带，inferred 与 asserted 在 manifest/决策输出中肉眼可区分
    attestation: str = ""
    n_classes_found: int = 0
    dropped_classes: list[str] = field(default_factory=list)

    def manifest_entry(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "image_library": self.source,
            "image_path": str(self.image_path),
            "label_source": self.label_source,
            "teacher_task": self.teacher_task,
            "attestation": self.attestation,
            "teacher_out_dir": str(self.teacher_out),
            "n_classes_found": self.n_classes_found,
            "dropped_classes": list(self.dropped_classes),
        }


def read_teacher_provenance(teacher_out: Path) -> dict[str, Any]:
    """读 ts_out 目录的来源清单（source.json，逐 case 可审计的事实源）。

    三元状态 provenance_status（S3P2-R2：attestation 层级，布尔
    provenance_missing 由同一状态同源推导，不维护两套事实）：
    - "missing"：source.json 缺失或该 case 不在 ct_lite 库级清单 evidence_scope
      内（S3R1：不默认 {"source": "total", "task": "total"}——"来源未知"不得
      变成"来源已知"；调用方按缺失处理，prepare_dataset 预检拒绝）。历史目录
      可显式补录来源并记录 backfill 依据，不删不重做
    - "inferred"：文件带 backfill=true 且无 attestation 字段（回填推断——
      mtime 窗口判据只能证"同一次拷贝"，不能证"同源产生"，证据等级低于实测）
    - "asserted"：文件带 attestation 字段（取该值），或旧实测写入（无
      attestation 无 backfill——write_teacher_source_json 实测写下的默认实测）
    - source.json 存在 -> 逐字段读出（source/task/experimental/created_by），
      prepare_dataset 把**实测来源**过许可门禁（不硬编码，防止 ts_out 目录里
      混入 nnInteractive 修正产物或 licensed 任务 mask 时门禁被绕过）
    - source.json 损坏（坏 JSON）-> DistillDataError（不猜来源）
    """
    meta_path = teacher_out.parent / "source.json"
    if not meta_path.exists():
        return {"provenance_status": "missing", "provenance_missing": True}
    try:
        doc = json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise DistillDataError(
            f"来源清单损坏（坏 JSON）：{meta_path}（不猜来源，不静默放行）"
        ) from e
    if not isinstance(doc, dict):
        raise DistillDataError(f"来源清单格式非法（非 dict）：{meta_path}")
    # S3P2-R2 ct_lite 库级清单（evidence_scope 枚举）：teacher_out.name 不在
    # cases 内的新增 case 按 missing 处理、不继承库级 provenance（scope 只
    # 覆盖回填时枚举的 case，新增目录回填后才有来源）
    if "cases" in doc and teacher_out.name not in doc["cases"]:
        return {"provenance_status": "missing", "provenance_missing": True}
    doc["provenance_status"] = provenance_status_of(doc)
    doc["provenance_missing"] = doc["provenance_status"] == "missing"
    return doc


def provenance_status_of(doc: dict[str, Any]) -> str:
    """source.json 字段 -> 三元状态（映射规则定死，read_teacher_provenance 共用）。

    - attestation 字段存在 -> 取该值，非 {"asserted","inferred"} 枚举 ->
      DistillDataError（不猜、不静默放行——手改/新写入方的非法值与坏 JSON
      同样拒绝，门禁不被绕过）
    - 无 attestation：backfill=true -> "inferred"（回填推断）；否则 -> "asserted"
      （旧实测写入，write_teacher_source_json 产出的默认实测）
    """
    if "attestation" in doc:
        att = doc["attestation"]
        if att not in ("asserted", "inferred"):
            raise DistillDataError(
                f"attestation 非法（{att!r} 不在 asserted/inferred 枚举，"
                "不猜、不静默放行）"
            )
        return str(att)
    return "inferred" if doc.get("backfill") is True else "asserted"


def write_teacher_source_json(teacher_out: Path, case_id: str) -> None:
    """写 ts_out 目录的来源清单（source.json，位置与 read_teacher_provenance 一致）。

    推理脚本在 run_total 成功后调用：新跑出的 ts_out 不再靠"缺文件默认 total"，
    来源清单是实测写下的。失败 case 不写（目录里无 source.json = 该 case 未
    成功产出，预检按 provenance_status 拒绝——S3R1 不默认 total）。

    attestation 层级（S3P2-R2）：实测写下 attestation="asserted" +
    evidence="run_output"；升级路径——覆盖 inferred（回填）文件时同样写
    asserted（asserted 只能被更新的 asserted 覆盖，回填脚本永不降级）。

    内容与 read_teacher_provenance 的实测读回约定对齐：
    {"source": "total", "task": "total", "created_by": ..., "case_id": ...,
     "attestation": "asserted", "evidence": "run_output"}
    """
    doc = {
        "source": "total",
        "task": "total",
        "created_by": "run_distill_teacher_inference.py",
        "case_id": case_id,
        "attestation": "asserted",
        "evidence": "run_output",
    }
    path = teacher_out.parent / "source.json"
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")


def missing_distill_classes(teacher_out: Path) -> list[str]:
    """ts_out 目录里缺失的蒸馏类名（DISTILL_CLASSES 值），齐全返回空列表。"""
    if not teacher_out.exists():
        return sorted(DISTILL_CLASSES.values())
    names = {f.name.removesuffix(".nii.gz") for f in teacher_out.glob("*.nii.gz")}
    return sorted(set(DISTILL_CLASSES.values()) - names)


def teacher_out_complete(teacher_out: Path) -> bool:
    """ts_out 目录完整性判定：117 类全量（每类一个 NIfTI）+ 15 蒸馏类齐全。

    两处共用（prepare_dataset 预检 + 推理脚本 missing 判定）：

    - 文件数 < 117（TS total 全量类数）-> False（部分输出：中断 / 部分类失败）
    - 缺任一蒸馏类文件（DISTILL_CLASSES 的 <类名>.nii.gz）-> False（蒸馏必需
      类缺失，合并后该类静默缺位）
    - 两项都满足 -> True
    """
    if not teacher_out.exists():
        return False
    files = sorted(teacher_out.glob("*.nii.gz"))
    if len(files) < 117:
        return False
    return not missing_distill_classes(teacher_out)


def build_train_pool(
    holdout_path: Path | None = None,
) -> list[DistillCase]:
    """训练池 = AMOS22 非 hold-out + CT-Lite 非 hold-out，泄漏即 raise。

    - 以 eval/holdout.txt 为准逐 case 排除（AMOS 40 例中 hold-out 15 例、
      CT-Lite 25 例中 hold-out 15 例）
    - 排除后用 holdout.check_leak 以**真实训练 case 列表**重查（proposal §8：
      训练集与 hold-out 交集必须为空，非空即中止）——排除逻辑有 bug 时本检查
      兜底中止
    """
    entries = load_holdout(holdout_path)
    holdout_ids = {e.case_id for e in entries}

    train: list[DistillCase] = []
    for p in sorted((AMOS / "imagesTr").glob("*.nii.gz")):
        cid = p.name.removesuffix(".nii.gz")
        if cid in holdout_ids:
            continue
        train.append(
            DistillCase(
                case_id=cid,
                source="amos",
                image_path=p,
                teacher_out=AMOS.parent / cid / "ts_out",
                label_source="total",
            )
        )
    for p in sorted((CT_LITE / "images").glob("*.nii.gz")):
        cid = p.name.removesuffix(".nii.gz")
        if cid in holdout_ids:
            continue
        train.append(
            DistillCase(
                case_id=cid,
                source="ct_lite",
                image_path=p,
                teacher_out=CT_LITE / "ts_out" / cid,
                label_source="total",
            )
        )

    # 泄漏重查（真实训练 case 列表 + 同一 holdout 文件，非空即中止；M3-REPORT
    # 遗留的「M4 以真实列表重查」在本函数落地。holdout_path 必须透传——排除与
    # 重查用同一份事实源，重查才是对排除逻辑的兜底）
    check_leak([c.case_id for c in train], path=holdout_path)
    return train


# ---------------------------------------------------------------------------
# 合并 + 落盘（nnU-Net 原始数据格式）
# ---------------------------------------------------------------------------

def merge_teacher_mask(
    teacher_out: Path,
    image_path: Path,
    keep_classes: set[str],
    case_id: str = "",
    img: Any | None = None,
) -> tuple[Any, list[str], list[str]]:
    """TS total 输出（每类一个 NIfTI）合并为单标签图，只保留 keep_classes。

    - 合并前每类 mask 与原图 affine 一致性校验（nifti_geom.assert_affine_match；
      不一致 raise，不静默重采样——proposal 铁律）
    - label 编码 = DISTILL_CLASSES 的值 1..15（nnU-Net labelsTr 与 AMOS 官方
      编码同源）
    - 多类同名 mask 重叠时先到先得（TS 类间本就互斥；重叠按序覆盖不报错）
    - keep_classes 之外的 TS 类**不静默丢弃**： dropped 类名由调用方记 manifest
    - 返回 (merged, dropped, found)：dropped = keep_classes 之外的类名，
      found = 实际找到的 keep_classes 类名（调用方据此记 manifest 的
      n_classes_found——真实找到的蒸馏类别数，非默认 0）
    - img 可传已加载的 nibabel 图（prepare_dataset 复用，省一次读盘解压）
    """
    import nibabel as nib
    import numpy as np

    from .nifti_geom import assert_affine_match

    if img is None:
        img = nib.load(str(image_path))
    label_value = {name: val for val, name in DISTILL_CLASSES.items()}
    seg_files = sorted(teacher_out.glob("*.nii.gz"))
    if not seg_files:
        raise DistillDataError(f"TS 输出目录无 mask：{teacher_out}")

    merged = np.zeros(img.shape, dtype=np.uint8)
    found: list[str] = []
    dropped: list[str] = []
    for f in seg_files:
        cls_name = f.name.removesuffix(".nii.gz")
        if cls_name not in keep_classes:
            dropped.append(cls_name)
            continue
        found.append(cls_name)
        mask_img = nib.load(str(f))
        assert_affine_match(img, mask_img, label=f"distill-merge {cls_name}（{case_id}）")
        arr = np.asarray(mask_img.dataobj) > 0
        merged[arr] = label_value[cls_name]
    return merged, dropped, found


def _safe_image_filename(case_id: str) -> str:
    """nnU-Net 文件名要求 [A-Za-z0-9._-]；case_id 已合规（amos_XXXX / sXXXX）。"""
    for ch in case_id:
        if not (ch.isalnum() or ch in "._-"):
            raise DistillDataError(f"case_id 含非法字符 {ch!r}：{case_id}")
    return case_id


def prepare_dataset(
    out_root: Path | None = None,
    settings: Any = None,
    holdout_path: Path | None = None,
    dry_run: bool = False,
    job_id: str = "",
) -> dict[str, Any]:
    """蒸馏数据集准备主入口：训练池 -> 泄漏重查 -> 逐 case 合并 -> nnU-Net 目录。

    - 训练标签只来自 TS total 教师 mask（许可门禁逐 case 强制：check_distill_source
      来源白名单 + 注册表 research_distill 资格）
    - AMOS labelsTr / CT-Lite masks/ 绝不读入训练标签
    - 输出 nnUNet_raw/Dataset501_BodyDonorDistill：imagesTr/ labelsTr/
      dataset.json + manifest.json（逐 case 来源清单，ModelVersion 审计用）
    - 不跑 TS 推理：AMOS 25 例教师 mask 需训练前现跑（eval 评估脚本已有
      ts_teacher_inference 同模式，训练侧在 nnUNetv2_train 前补齐——见
      scripts/prepare_distill_dataset.py）
    """
    import time as _time

    import nibabel as nib
    import numpy as np

    from .nifti_geom import assert_affine_match

    t0 = _time.monotonic()
    keep_classes = set(DISTILL_CLASSES.values())
    pool = build_train_pool(holdout_path)

    raw_root = Path(out_root) if out_root else (Path.home() / "data" / "BodyDonor" / "nnUNet_raw")
    ds_dir = raw_root / DISTILL_DATASET_NAME
    images_tr = ds_dir / "imagesTr"
    labels_tr = ds_dir / "labelsTr"
    if not dry_run:
        images_tr.mkdir(parents=True, exist_ok=True)
        labels_tr.mkdir(parents=True, exist_ok=True)

    manifest_cases: list[dict[str, Any]] = []
    missing_teacher: list[str] = []
    missing_detail: list[tuple[str, list[str]]] = []
    total_dropped: dict[str, int] = {}

    # 预检：先扫全部 case 的来源与教师 mask 存在性（不落盘）——任何许可门禁
    # 拒绝或缺失教师 mask 都在写第一个文件之前 raise，不留下 manifest 不覆盖的
    # 半成品数据目录（ModelVersion 审计链完整性）
    provenances: dict[str, dict[str, Any]] = {}
    for c in pool:
        prov = read_teacher_provenance(c.teacher_out)
        provenances[c.case_id] = prov
        if prov.get("provenance_status") == "missing":
            # S3R1：缺 source.json（或 ct_lite scope 外新增 case）= 来源未知，
            # 不默认 total 进训练清单（历史目录先补录来源 + backfill 依据，
            # 再跑 prepare_dataset）
            raise DistillDataError(
                f"来源清单缺失 {c.case_id}：{c.teacher_out.parent / 'source.json'}"
                "（不默认 total——来源未知不冒充已知；先补录来源并记录依据）"
            )
        # S3P2-R2：inferred（回填推断）可用于研究，manifest 条目必须带
        # attestation 使其肉眼可区分；asserted（实测）直接放行
        c.attestation = str(prov.get("provenance_status", ""))
        # 许可门禁逐 case 强制：来源清单实测值（非硬编码）过白名单
        ok, reason = check_distill_source(
            str(prov.get("source", "")), teacher_task=str(prov.get("task", "")),
            experimental=bool(prov.get("experimental", False)),
        )
        if not ok:
            raise DistillDataError(f"许可门禁拒绝 {c.case_id}：{reason}")
        c.label_source = str(prov.get("source", ""))
        c.teacher_task = str(prov.get("task", ""))
        # 完整性预检（teacher_out_complete 共用判定）：117 类全量 + 15 蒸馏类
        # 齐全才放行——缺 15 个蒸馏类的部分目录不静默通过（缺类合并后静默缺位，
        # manifest n_classes_found 失真）
        if not teacher_out_complete(c.teacher_out):
            missing_teacher.append(c.case_id)
            missing_detail.append((c.case_id, missing_distill_classes(c.teacher_out)))
    if missing_teacher:
        detail = "；".join(
            (
                f"{cid} 缺蒸馏类 {cls[:5]}{'…' if len(cls) > 5 else ''}"
                if cls else f"{cid} 无/不全量 mask"
            )
            for cid, cls in missing_detail[:5]
        )
        raise DistillDataError(
            f"{len(missing_teacher)} 个训练 case 的 TS total 教师 mask 不完整（先跑教师"
            "推理补齐，不静默跳过）："
            f"{missing_teacher[:10]}{'…' if len(missing_teacher) > 10 else ''}"
            f"（{detail}）"
        )

    for c in pool:
        # 原图只加载一次：merge 复用 + imagesTr 落盘复用（省一次读盘解压）
        img = nib.load(str(c.image_path))
        merged, dropped, found = merge_teacher_mask(
            c.teacher_out, c.image_path, keep_classes, c.case_id, img=img
        )
        for cls in dropped:
            total_dropped[cls] = total_dropped.get(cls, 0) + 1
        # manifest 逐 case 记真实找到的蒸馏类别数（非默认 0；found 由
        # merge_teacher_mask 实测，缺失的类静默缺位即 manifest 失真）
        c.n_classes_found = len(found)

        if not dry_run:
            base = _safe_image_filename(c.case_id)
            # 原图复制进 imagesTr（nnU-Net 命名 {case}_0000.nii.gz，0000=CT 模态）
            img_nib = nib.Nifti1Image(np.asarray(img.dataobj), img.affine)
            img_out = images_tr / f"{base}_0000.nii.gz"
            nib.save(img_nib, str(img_out))
            # labelsTr 落盘：读回校验 affine 一致（M1-fix (b) 守卫语义）
            img_check = nib.load(str(img_out))
            lab_out = labels_tr / f"{base}.nii.gz"
            lab_nib = nib.Nifti1Image(merged, img.affine)
            nib.save(lab_nib, str(lab_out))
            lab_check = nib.load(str(lab_out))
            assert_affine_match(img_check, lab_check, label=f"distill labels {c.case_id}")

        entry = c.manifest_entry()
        entry["dropped_classes"] = dropped
        entry["experimental_flag"] = bool(provenances[c.case_id].get("experimental", False))
        manifest_cases.append(entry)

    n_written = len(manifest_cases)
    # nnU-Net dataset.json（labels 直接给 1-15 名单；modalities CT）
    dataset_json = {
        "channel_names": {"0": "ct"},
        "labels": {
            "background": 0,
            **{name: val for val, name in sorted(DISTILL_CLASSES.items())},
        },
        "numTraining": n_written,
        "file_ending": ".nii.gz",
        "name": DISTILL_DATASET_NAME,
        "description": (
            "BodyDonor M4 distillation: TS total teacher masks on AMOS22/CT-Lite "
            "non-holdout cases (commercial-safe path, LICENSES §8)"
        ),
        "reference": "LICENSES-THIRD-PARTY.md §8; proposal.md §6",
        "licence": (
            "labels: TS total teacher output (Apache-2.0 weights, any usage); "
            "images: AMOS22 CC-BY-4.0 (Zenodo 7262581), CT-Lite CC-BY-4.0"
        ),
        "release": "0.1.0",
    }

    manifest = {
        "dataset": DISTILL_DATASET_NAME,
        "label_policy": {
            "allowed_sources": sorted(ALLOWED_TRAIN_SOURCES),
            "rejected": sorted(REJECTED_SOURCES),
            "amos_labelsTr_never_in_training": True,
            "ct_lite_masks_never_in_training": True,
        },
        "class_scope": {
            "n_classes": len(DISTILL_CLASSES),
            "classes": {str(k): v for k, v in sorted(DISTILL_CLASSES.items())},
            "rationale": (
                "收敛到 hold-out 门禁需要的 AMOS 15 类：训练池仅 35 例，117 类全量"
                "每类样本过低（CT-Lite 10 例频次实测最高 heart 7/10）；AMOS 15 类"
                "覆盖门禁评估类别（AMOS_LABEL_MAP 同源），不静默丢类——排除清单"
                "见 excluded_classes"
            ),
        },
        "excluded_classes": {
            cls: n for cls, n in sorted(total_dropped.items(), key=lambda kv: -kv[1])
        },
        "leak_check": {
            "holdout_file": "eval/holdout.txt",
            "rechecked_with_real_train_list": True,
            "train_pool_amos": sum(1 for c in pool if c.source == "amos"),
            "train_pool_ct_lite": sum(1 for c in pool if c.source == "ct_lite"),
        },
        "gpu_lock": {
            "task": "nnunet",
            "job_id": job_id,
            "note": (
                "本 manifest 记录数据准备阶段（CPU 合并，不占 GPU）；nnU-Net 预处理"
                "与训练前的 GPU 锁检查由训练侧执行（gpu_lock.acquire batch 模式，"
                "任务名 nnunet），不在本 manifest 审计范围内"
            ),
        },
        "provenance_convention": (
            "逐 case ts_out 旁 source.json 为来源事实源（缺失即来源未知，不默认"
            " total；缺失 case 在 prepare_dataset 预检阶段即拒绝，历史目录补录来源"
            "须记 backfill 依据）；非白名单来源同在预检阶段即拒绝"
        ),
        "n_cases": n_written,
        "cases": manifest_cases,
        "elapsed_s": None,
    }

    if not dry_run:
        (ds_dir / "dataset.json").write_text(
            json.dumps(dataset_json, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        manifest_path = ds_dir / "manifest.json"
        manifest["elapsed_s"] = round(_time.monotonic() - t0, 1)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    return {
        "dataset_dir": ds_dir,
        "manifest_path": ds_dir / "manifest.json",
        "n_cases": n_written,
        "train_pool_amos": sum(1 for c in pool if c.source == "amos"),
        "train_pool_ct_lite": sum(1 for c in pool if c.source == "ct_lite"),
        "missing_teacher": missing_teacher,
    }


__all__ = [
    "ALLOWED_TRAIN_SOURCES",
    "ALLOWED_TRAIN_SOURCES",
    "DISTILL_CLASSES",
    "DISTILL_DATASET_ID",
    "DISTILL_DATASET_NAME",
    "DistillCase",
    "DistillDataError",
    "REJECTED_SOURCES",
    "build_train_pool",
    "check_distill_source",
    "mask_allowed_in_distill_training_strict",
    "merge_teacher_mask",
    "missing_distill_classes",
    "nnunet_label_values",
    "prepare_dataset",
    "provenance_status_of",
    "read_teacher_provenance",
    "teacher_out_complete",
    "write_teacher_source_json",
]
