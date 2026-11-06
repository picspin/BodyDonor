"""本体映射（M2，proposal §3）：自然语言标签 -> TotalSegmentator 类别的静态查表。

机制：

- **版本化静态 YAML 查表**：`ontology/{version}/map.yaml`，git 管、评审可 diff。
  不引重型本体服务（全量 SNOMED/Uberon 图谱是 M5 之后的事）。
- 条目字段：`nl_label / synonyms / snomed_ct / uberon / totalseg_class / status`。
  **中英双语同义词进同一张表**（synonyms 字段，Q1 已定）。
- **三态回退**：对每个 NL 标签返回
  - `resolved`：唯一命中 totalseg_class
  - `ambiguous`：多个候选（如"肝"命中 liver 与 hepatic vessel），附同义词与
    SNOMED 概念 ID，交回用户点选；**用户选择写回映射表**，下次直接 resolved
    （这是产品资产，不是补丁）
  - `unresolved`：零命中
- **逃生通道（SegVol/SAT 类真文本提示模型）**：默认关闭，须显式开启
  （--experimental-text-prompt 或 config）；结果打 `experimental` 标记，
  **绝不进蒸馏训练集**。M2 只落地开关/标记/隔离逻辑，不真正接 SegVol
  （其权重许可待核实，LICENSES-THIRD-PARTY.md §6）。

写回策略（最小合理选择）：用户消歧结果写回 `ontology/{version}/overrides.yaml`
（同一版本目录，与主表分离，git diff 只见用户决策）。查询时 overrides 优先于主表。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from .config import Settings

ONTOLOGY_VERSION = "v1"
ONTOLOGY_DIR = Path(__file__).resolve().parents[2] / "ontology"
OVERRIDES_FILENAME = "overrides.yaml"

# 三态
RESOLVED = "resolved"
AMBIGUOUS = "ambiguous"
UNRESOLVED = "unresolved"

MapStatus = Literal["active", "deprecated"]


@dataclass
class MapEntry:
    """映射表条目：NL 标签 -> TotalSegmentator 类别 + 本体编码。"""

    nl_label: str
    totalseg_class: str
    synonyms: list[str] = field(default_factory=list)  # 中英双语同义词
    snomed_ct: str | None = None
    uberon: str | None = None
    status: MapStatus = "active"

    def to_dict(self) -> dict[str, Any]:
        return {
            "nl_label": self.nl_label,
            "synonyms": list(self.synonyms),
            "snomed_ct": self.snomed_ct,
            "uberon": self.uberon,
            "totalseg_class": self.totalseg_class,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MapEntry:
        return cls(
            nl_label=str(d["nl_label"]),
            totalseg_class=str(d["totalseg_class"]),
            synonyms=[str(s) for s in d.get("synonyms", [])],
            snomed_ct=d.get("snomed_ct"),
            uberon=d.get("uberon"),
            status=d.get("status", "active"),
        )


@dataclass
class MapCandidate:
    """ambiguous 态的候选（交回用户点选）。"""

    entry: MapEntry
    matched_term: str  # 命中的同义词/主标签

    def to_dict(self) -> dict[str, Any]:
        return {
            "nl_label": self.entry.nl_label,
            "totalseg_class": self.entry.totalseg_class,
            "snomed_ct": self.entry.snomed_ct,
            "uberon": self.entry.uberon,
            "synonyms": list(self.entry.synonyms),
            "matched_term": self.matched_term,
        }


@dataclass
class MapResult:
    """映射服务对单个 NL 标签的三态结果。"""

    state: str  # resolved | ambiguous | unresolved
    label: str
    entry: MapEntry | None = None  # resolved 时的命中
    candidates: list[MapCandidate] = field(default_factory=list)  # ambiguous 时的候选
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "label": self.label,
            "entry": self.entry.to_dict() if self.entry else None,
            "candidates": [c.to_dict() for c in self.candidates],
            "reason": self.reason,
        }


class OntologyError(Exception):
    """本体映射失败（版本不存在、YAML 解析失败等）。"""


def load_map(version: str = ONTOLOGY_VERSION, ontology_dir: Path | None = None) -> list[MapEntry]:
    """加载 `ontology/{version}/map.yaml` 全部条目。"""
    base = ontology_dir or ONTOLOGY_DIR
    path = base / version / "map.yaml"
    if not path.exists():
        raise OntologyError(f"本体映射表不存在：{path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise OntologyError(f"映射表 YAML 解析失败：{e}") from e
    entries_raw = raw.get("entries", [])
    if not isinstance(entries_raw, list):
        raise OntologyError("map.yaml 格式错误：entries 必须是列表")
    entries = [MapEntry.from_dict(d) for d in entries_raw]
    # deprecated 条目不参与查询（保留在表里供 diff/回溯）
    return [e for e in entries if e.status == "active"]


def load_overrides(
    version: str = ONTOLOGY_VERSION, ontology_dir: Path | None = None
) -> dict[str, MapEntry]:
    """加载用户消歧写回表 `ontology/{version}/overrides.yaml`。

    key = nl_label（规范化小写），value = 用户点选的条目。
    """
    base = ontology_dir or ONTOLOGY_DIR
    path = base / version / OVERRIDES_FILENAME
    if not path.exists():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise OntologyError(f"overrides.yaml 解析失败：{e}") from e
    entries_raw = raw.get("overrides", [])
    out: dict[str, MapEntry] = {}
    for d in entries_raw:
        entry = MapEntry.from_dict(d)
        out[normalize_label(entry.nl_label)] = entry
    return out


def normalize_label(label: str) -> str:
    """标签规范化：strip + 小写（中文字符不受影响）。"""
    return label.strip().lower()


class OntologyService:
    """映射服务：查询三态 + 用户消歧写回。CLI 与 API 共用。"""

    def __init__(
        self,
        settings: Settings,
        version: str = ONTOLOGY_VERSION,
        ontology_dir: Path | None = None,
    ):
        self.settings = settings
        self.version = version
        self.ontology_dir = ontology_dir or ONTOLOGY_DIR
        self._entries = load_map(version, self.ontology_dir)
        self._overrides = load_overrides(version, self.ontology_dir)
        # 索引：主标签 + 每个同义词 -> entry（同义词可命中多条 -> ambiguous）
        self._index: dict[str, list[MapEntry]] = {}
        for e in self._entries:
            for term in [e.nl_label, *e.synonyms]:
                key = normalize_label(term)
                bucket = self._index.setdefault(key, [])
                if e not in bucket:
                    bucket.append(e)

    # -- 查询 ---------------------------------------------------------------

    def resolve(self, label: str) -> MapResult:
        """对单个 NL 标签返回三态结果（overrides 优先于主表）。"""
        key = normalize_label(label)
        if not key:
            return MapResult(state=UNRESOLVED, label=label, reason="空标签")

        # 1. overrides 优先（用户已消歧过）
        if key in self._overrides:
            entry = self._overrides[key]
            return MapResult(
                state=RESOLVED, label=label, entry=entry, reason="用户消歧写回（override）"
            )

        hits = self._index.get(key, [])
        if not hits:
            return MapResult(state=UNRESOLVED, label=label, reason="无候选（可开启实验逃生通道）")
        if len(hits) == 1:
            return MapResult(state=RESOLVED, label=label, entry=hits[0], reason="唯一命中")
        return MapResult(
            state=AMBIGUOUS,
            label=label,
            candidates=[
                MapCandidate(entry=e, matched_term=key) for e in hits
            ],
            reason=f"{len(hits)} 个候选，需用户点选（选择写回 overrides）",
        )

    def resolve_batch(self, labels: list[str]) -> list[MapResult]:
        """批量查询。"""
        return [self.resolve(lb) for lb in labels]

    # -- 写回 ---------------------------------------------------------------

    def disambiguate(
        self,
        label: str,
        totalseg_class: str,
        ontology_dir: Path | None = None,
        snomed_ct: str | None = None,
        uberon: str | None = None,
        synonyms: list[str] | None = None,
    ) -> MapEntry:
        """ambiguous -> 用户点选 -> 写回 overrides.yaml（下次 resolve 直接 resolved）。

        totalseg_class 必须来自该标签的实际候选（防写回错误类别）。
        """
        result = self.resolve(label)
        already = (
            result.state == RESOLVED
            and result.entry is not None
            and result.entry.totalseg_class == totalseg_class
        )
        if already:
            # 已是该类别：幂等写回（仍记录，供审计）
            entry = result.entry
        elif result.state == AMBIGUOUS:
            match = [c.entry for c in result.candidates if c.entry.totalseg_class == totalseg_class]
            if not match:
                raise OntologyError(
                    f"写回失败：totalseg_class '{totalseg_class}' 不是 '{label}' 的候选"
                    f"（候选：{[c.entry.totalseg_class for c in result.candidates]}）"
                )
            entry = match[0]
        else:
            raise OntologyError(
                f"写回失败：'{label}' 当前是 {result.state} 态，无候选可写回"
            )

        # 覆盖条目：用户点选结果 + 可选补充字段
        chosen = MapEntry(
            nl_label=label.strip(),
            totalseg_class=entry.totalseg_class,
            synonyms=synonyms if synonyms is not None else list(entry.synonyms),
            snomed_ct=snomed_ct or entry.snomed_ct,
            uberon=uberon or entry.uberon,
            status="active",
        )
        self._write_override(chosen, ontology_dir)
        self._overrides[normalize_label(label)] = chosen
        return chosen

    def _write_override(self, entry: MapEntry, ontology_dir: Path | None = None) -> None:
        """把用户消歧结果写进 overrides.yaml（同一版本目录，与主表分离）。"""
        base = ontology_dir or self.ontology_dir
        out_dir = base / self.version
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / OVERRIDES_FILENAME
        # 读现有文件（不经过 self._overrides，保留磁盘上已有内容）
        existing: list[MapEntry] = []
        if path.exists():
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            existing = [MapEntry.from_dict(d) for d in raw.get("overrides", [])]
        key = normalize_label(entry.nl_label)
        existing = [e for e in existing if normalize_label(e.nl_label) != key]
        existing.append(entry)
        doc = {
            "version": self.version,
            "note": "用户消歧写回（ambiguous -> 用户点选）；此文件是产品资产",
            "overrides": [e.to_dict() for e in existing],
        }
        path.write_text(
            yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )

    # -- 逃生通道（experimental） --------------------------------------------

    def escape_hatch(self, label: str) -> dict[str, Any]:
        """experimental 逃生通道入口（SegVol/SAT 类真文本提示模型）。

        M2 只落地开关/标记/隔离逻辑，不真正接 SegVol（其权重许可待核实）。
        - 默认关闭：未显式开启时拒绝（config experimental_text_prompt.enabled=false）
        - 开启时返回打 `experimental` 标记的占位结果；该标记的产物**绝不进蒸馏训练集**
          （蒸馏训练集准入检查 must 拒收 experimental mask，见 tests）
        """
        enabled = self.settings.experimental_text_prompt.enabled
        if not enabled:
            return {
                "allowed": False,
                "state": UNRESOLVED,
                "label": label,
                "reason": "实验逃生通道未开启（experimental_text_prompt.enabled=false）；"
                "开启后结果打 experimental 标记，不进蒸馏训练集",
                "experimental": False,
            }
        # 真文本提示模型接入选 M3+（SegVol 权重许可待核实）。此处占位：返回
        # experimental 标记 + unresolved（调用方不应把占位结果当真 mask 用）。
        return {
            "allowed": True,
            "state": UNRESOLVED,
            "label": label,
            "reason": "逃生通道已开启，但文本提示模型未接入（M2 只落地开关与隔离逻辑）",
            "experimental": True,
        }

    def summary(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "n_entries": len(self._entries),
            "n_overrides": len(self._overrides),
            "experimental_text_prompt": self.settings.experimental_text_prompt.enabled,
        }


# ---------------------------------------------------------------------------
# 蒸馏训练集准入检查（experimental 隔离的强制点）
# ---------------------------------------------------------------------------

def mask_allowed_in_distill_training(mask_meta: dict[str, Any]) -> tuple[bool, str]:
    """蒸馏训练集准入检查：experimental 标记的 mask 绝不进训练集。

    mask_meta 需含 'experimental'（bool）与 'source'（teacher/student/manual/...）。
    返回 (allowed, reason)。worker/训练路径在入集前必须调用此函数
    （与任务白名单 check_task 同级的硬约束）。
    """
    if mask_meta.get("experimental"):
        return False, "experimental 标记的 mask 不进蒸馏训练集（逃生通道产物隔离）"
    source = mask_meta.get("source", "")
    if source == "teacher":
        # teacher mask 还要看任务是否在商用蒸馏白名单（调用方传 task）
        task = mask_meta.get("task", "")
        if task and task != "total":
            # 商用蒸馏只允许 total（license_registry / settings.teacher）
            return False, f"teacher 任务 '{task}' 不在商用蒸馏白名单（仅 total）"
    return True, "ok"


__all__ = [
    "AMBIGUOUS",
    "ONTOLOGY_DIR",
    "ONTOLOGY_VERSION",
    "OVERRIDES_FILENAME",
    "RESOLVED",
    "UNRESOLVED",
    "MapCandidate",
    "MapEntry",
    "MapResult",
    "OntologyError",
    "OntologyService",
    "load_map",
    "load_overrides",
    "mask_allowed_in_distill_training",
    "normalize_label",
]
