"""本体映射（M2 + M2-align 3.4）：自然语言标签 -> TotalSegmentator 类别。

机制：

- **版本化静态 YAML 查表**：`ontology/{version}/map.yaml`（v1：117 条目，
  TS total 全类覆盖），git 管、评审可 diff。不引重型本体服务。
- 条目字段：`nl_label / synonyms / snomed_ct / uberon / totalseg_class / status`。
  **中英双语同义词进同一张表**（synonyms 字段，Q1 已定）。
- **解析分层（M2-align 3.4，先离线后可插拔）**：
  1. 离线精确查表：label/synonym 精确命中。
  2. 离线模糊查表：子串/编辑距离（difflib，阈值 `FUZZY_CUTOFF`，多候选 ambiguous）。
  3. 简单规则解析：分隔符多标签（"肝和脾"、"肝、脾"）、左右修饰（"左肾"/"right
     kidney"）、常见缩写口语（"胆"→ambiguous，不静默单解）。
  4. 可插拔 LLM 意图解析：`IntentParser` 接口（NL 串 -> 候选 totalseg_class +
     置信度），`RuleIntentParser`（1–3 层）为默认；LLM 实现只留注册点，
     本轮不调任何 LLM、不联网。
- **三态回退**：
  - `resolved`：唯一命中 totalseg_class
  - `ambiguous`：多个候选，交回用户点选；**选择写回 overrides.yaml**，下次 resolved
  - `unresolved`：零命中
- **逃生通道（SegVol/SAT 类真文本提示模型）**：默认关闭，须显式开启；结果打
  `experimental` 标记，**绝不进蒸馏训练集**。

写回策略：用户消歧结果写回 `ontology/{version}/overrides.yaml`（同一版本目录，
与主表分离，git diff 只见用户决策）。查询时 overrides 优先于主表。
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

import yaml

from .config import Settings

ONTOLOGY_VERSION = "v1"
ONTOLOGY_DIR = Path(__file__).resolve().parents[2] / "ontology"
OVERRIDES_FILENAME = "overrides.yaml"

# 三态
RESOLVED = "resolved"
AMBIGUOUS = "ambiguous"
UNRESOLVED = "unresolved"

# M2-align 3.4：离线模糊查表阈值（difflib SequenceMatcher ratio 下限；config 化后置，
# 阈值改动 = config/代码常量 diff，测试锁定）
FUZZY_CUTOFF = 0.6

# 常见缩写/口语 -> 候选（M2-align 3.4 第 3 层：缩写不静默单解，进 ambiguous）
ABBREVIATIONS: dict[str, list[str]] = {
    "胆": ["gallbladder", "liver"],  # "胆"口语可指胆囊或胆道，不静默单解
    "胆道": ["gallbladder"],
    "kidney": ["kidney_left", "kidney_right"],  # 无左右修饰的 kidney 双候选
    "肾": ["kidney_left", "kidney_right"],
    "adrenal gland": ["adrenal_gland_left", "adrenal_gland_right"],
    "肾上腺": ["adrenal_gland_left", "adrenal_gland_right"],
    "femur": ["femur_left", "femur_right"],
    "股骨": ["femur_left", "femur_right"],
    "lung": ["lung_upper_lobe_left", "lung_upper_lobe_right",
             "lung_lower_lobe_left", "lung_lower_lobe_right",
             "lung_middle_lobe_right"],
    "肺": ["lung_upper_lobe_left", "lung_upper_lobe_right",
           "lung_lower_lobe_left", "lung_lower_lobe_right",
           "lung_middle_lobe_right"],
}

# 左右修饰（M2-align 3.4 第 3 层）：前缀/后缀 -> (侧, 剥离后标签)
LEFT_RIGHT_PATTERNS = [
    # 中文前缀
    (re.compile(r"^左(?:侧)?(.+)$"), "left"),
    (re.compile(r"^右(?:侧)?(.+)$"), "right"),
    # 英文前缀
    (re.compile(r"^left\s+(.+)$", re.IGNORECASE), "left"),
    (re.compile(r"^right\s+(.+)$", re.IGNORECASE), "right"),
    # 中文后缀
    (re.compile(r"^(.+?)左侧$"), "left"),
    (re.compile(r"^(.+?)右侧$"), "right"),
]

# 多标签分隔符（M2-align 3.4 第 3 层）：、，,；; 和 与 跟 + & / 以及英文 and
# （\band\b 词边界已够；"and" 出现在类别名内部的场景走精确命中优先，不受影响）
MULTI_LABEL_SPLIT = re.compile(r"[、,，;；和与跟\+&/]|和(?![a-zA-Z])|\band\b", re.IGNORECASE)

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


# ---------------------------------------------------------------------------
# M2-align 3.4 第 4 层：可插拔 LLM 意图解析接口（本轮只留注册点，不调 LLM）
# ---------------------------------------------------------------------------

@dataclass
class IntentCandidate:
    """意图解析候选（totalseg_class + 置信度）。"""

    totalseg_class: str
    confidence: float  # 0..1


class IntentParser(Protocol):
    """可插拔意图解析接口（M2-align 3.4 第 4 层）。

    输入自然语言串，输出候选 totalseg_class 列表 + 置信度。默认实现
    `RuleIntentParser`（离线 1–3 层）；LLM 实现（联网/本地推理）只留注册点，
    本轮不调任何 LLM、不联网——实现方自行注册（见 register_intent_parser）。
    """

    def parse(self, text: str) -> list[IntentCandidate]:
        """解析 NL 串 -> 候选列表（按置信度降序；空串/无法解析返回 []）。"""
        ...


class RuleIntentParser:
    """默认实现：离线规则解析（分层 1–3：多标签 -> 精确/模糊查表 -> 左右修饰）。"""

    def __init__(self, service: OntologyService):
        self.service = service

    def parse(self, text: str) -> list[IntentCandidate]:
        """解析 NL 串 -> 候选列表（多标签切分后逐个 resolve，resolved 优先）。

        - resolved：confidence 1.0（唯一命中）
        - ambiguous：取全部候选，confidence 0.5（需用户点选）
        - unresolved：丢弃（fuzzy 无命中）
        """
        out: list[IntentCandidate] = []
        seen: set[str] = set()
        for sub in split_multi_label(text):
            result = self.service.resolve(sub)
            if result.state == RESOLVED and result.entry is not None:
                if result.entry.totalseg_class not in seen:
                    out.append(
                        IntentCandidate(result.entry.totalseg_class, 1.0)
                    )
                    seen.add(result.entry.totalseg_class)
            elif result.state == AMBIGUOUS:
                for c in result.candidates:
                    if c.entry.totalseg_class not in seen:
                        out.append(
                            IntentCandidate(c.entry.totalseg_class, 0.5)
                        )
                        seen.add(c.entry.totalseg_class)
        return out


# LLM 意图解析注册点（M2-align 3.4 第 4 层）：LLM 实现方调用此函数注册；
# 本轮无人注册（不联网、不调 LLM），get_intent_parser 返回 RuleIntentParser。
_intent_parser_factory: Any = None


def register_intent_parser(factory: Any) -> None:
    """注册 LLM 意图解析工厂（`factory() -> IntentParser`）。本轮不调用。

    注册后 get_intent_parser 返回工厂产物（LLM 实现）；未注册则用默认
    RuleIntentParser（离线规则，断网可跑）。
    """
    global _intent_parser_factory
    _intent_parser_factory = factory


def get_intent_parser(service: OntologyService) -> IntentParser:
    """取当前意图解析器：注册的 LLM 工厂优先，否则默认 RuleIntentParser。"""
    if _intent_parser_factory is not None:
        return _intent_parser_factory(service)
    return RuleIntentParser(service)


def split_multi_label(text: str) -> list[str]:
    """分隔符多标签切分（M2-align 3.4 第 3 层）："肝和脾" -> ["肝", "脾"]。

    分隔符：、，,；; 与 跟 + & /；无分隔符返回 [原文]。
    """
    text = text.strip()
    if not text:
        return []
    parts = [p.strip() for p in MULTI_LABEL_SPLIT.split(text) if p.strip()]
    return parts or [text]


def _apply_left_right(label: str) -> tuple[str, str | None]:
    """左右修饰剥离（M2-align 3.4 第 3 层）："左肾" -> ("肾", "left")。

    返回 (剥离后标签, side | None)。无修饰返回 (原标签, None)。
    """
    for pattern, side in LEFT_RIGHT_PATTERNS:
        m = pattern.match(label)
        if m:
            stripped = m.group(1).strip()
            if stripped:  # 纯 "左"/"right" 不剥离
                return stripped, side
    return label, None


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
        """对单个 NL 标签返回三态结果（M2-align 3.4 分层解析）。

        顺序：overrides -> 精确查表 -> 缩写表 -> 左右修饰 + 精确 -> 模糊查表。
        """
        key = normalize_label(label)
        if not key:
            return MapResult(state=UNRESOLVED, label=label, reason="空标签")

        # 1. overrides 优先（用户已消歧过）
        if key in self._overrides:
            entry = self._overrides[key]
            return MapResult(
                state=RESOLVED, label=label, entry=entry, reason="用户消歧写回（override）"
            )

        # 2. 离线精确查表（label/synonym 精确命中）
        hits = self._index.get(key, [])
        if len(hits) == 1:
            return MapResult(state=RESOLVED, label=label, entry=hits[0], reason="唯一命中")

        # 3. 简单规则解析：常见缩写/口语（不静默单解，进 ambiguous）
        if not hits and key in ABBREVIATIONS:
            abbr_hits = [
                e
                for cls in ABBREVIATIONS[key]
                for e in self._entries
                if e.totalseg_class == cls
            ]
            if len(abbr_hits) > 1:
                return MapResult(
                    state=AMBIGUOUS,
                    label=label,
                    candidates=[MapCandidate(entry=e, matched_term=key) for e in abbr_hits],
                    reason=f"缩写/口语 '{label}' 有 {len(abbr_hits)} 个候选，需用户点选"
                    "（不许静默单解）",
                )
            if abbr_hits:
                return MapResult(
                    state=RESOLVED, label=label, entry=abbr_hits[0], reason="缩写表唯一命中"
                )

        # 4. 左右修饰剥离 + 精确查表（"左肾" -> "肾" + left -> kidney_left；
        #    "left femur" -> "femur" + left -> femur_left）
        stripped, side = _apply_left_right(key)
        if side and stripped != key:
            # 被修饰词在缩写表（如"肾"/"femur"）-> side 拼类别
            base_classes = ABBREVIATIONS.get(stripped, [])
            if base_classes:
                matched = [
                    e
                    for cls in base_classes
                    for e in self._entries
                    if e.totalseg_class == cls
                    and (
                        (side == "left" and cls.endswith("_left"))
                        or (side == "right" and cls.endswith("_right"))
                    )
                ]
                if len(matched) == 1:
                    return MapResult(
                        state=RESOLVED,
                        label=label,
                        entry=matched[0],
                        reason=f"左右修饰解析（{side}）",
                    )
            # 被修饰词是普通表条目（如"侧肾"）-> 剥离后精确命中
            lr_hits = self._index.get(stripped, [])
            if lr_hits and len(lr_hits) == 1:
                return MapResult(
                    state=RESOLVED, label=label, entry=lr_hits[0], reason="修饰词剥离唯一命中"
                )

        # 5. 离线模糊查表（子串/编辑距离，difflib；阈值 FUZZY_CUTOFF）
        if not hits:
            return self._fuzzy_resolve(label, key)

        # 精确命中多条 -> ambiguous
        return MapResult(
            state=AMBIGUOUS,
            label=label,
            candidates=[MapCandidate(entry=e, matched_term=key) for e in hits],
            reason=f"{len(hits)} 个候选，需用户点选（选择写回 overrides）",
        )

    def _fuzzy_resolve(self, label: str, key: str) -> MapResult:
        """离线模糊查表（M2-align 3.4 第 2 层）：子串/编辑距离，阈值 FUZZY_CUTOFF。

        多候选（>=2 个并列最高分）走 ambiguous 三态；唯一最高命中 resolved；
        零命中 unresolved。
        """
        candidates: list[tuple[float, MapEntry, str]] = []
        for e in self._entries:
            best_ratio = 0.0
            best_term = ""
            for term in [e.nl_label, *e.synonyms]:
                t = normalize_label(term)
                ratio = difflib.SequenceMatcher(None, key, t).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_term = t
            if best_ratio >= FUZZY_CUTOFF:
                candidates.append((best_ratio, e, best_term))
        if not candidates:
            return MapResult(
                state=UNRESOLVED, label=label, reason="无候选（精确/模糊均未命中）"
            )
        candidates.sort(key=lambda x: (-x[0], x[2]))
        top = candidates[0][0]
        # 并列最高分多候选 -> ambiguous（不许静默单解）
        top_hits = [c for c in candidates if abs(c[0] - top) < 1e-9]
        if len(top_hits) > 1:
            return MapResult(
                state=AMBIGUOUS,
                label=label,
                candidates=[
                    MapCandidate(entry=e, matched_term=t) for _, e, t in top_hits
                ],
                reason=f"模糊匹配 {len(top_hits)} 个并列候选（ratio={top:.2f}），需用户点选",
            )
        _, entry, term = top_hits[0]
        return MapResult(
            state=RESOLVED,
            label=label,
            entry=entry,
            reason=f"模糊匹配命中（{term}，ratio={top:.2f}，阈值 {FUZZY_CUTOFF}）",
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
    "FUZZY_CUTOFF",
    "ONTOLOGY_DIR",
    "ONTOLOGY_VERSION",
    "OVERRIDES_FILENAME",
    "RESOLVED",
    "UNRESOLVED",
    "IntentCandidate",
    "IntentParser",
    "MapCandidate",
    "MapEntry",
    "MapResult",
    "OntologyError",
    "OntologyService",
    "RuleIntentParser",
    "get_intent_parser",
    "load_map",
    "load_overrides",
    "mask_allowed_in_distill_training",
    "normalize_label",
    "register_intent_parser",
    "split_multi_label",
]
