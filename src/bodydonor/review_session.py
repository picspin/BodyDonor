"""M5 审阅会话状态模型（M5-a/b/c/d + 修正传播 + 异常驱动队列）。

纯逻辑模块（只依赖 numpy，不 import torch / nnInteractive / FastAPI），
前端 review.html 与后端 app.py 共用同一语义：

- M5-a：EditMode + 键盘映射 + 空格平移优先 + Esc 不清提示（PromptSession）。
- M5-b：per-class 草稿生命周期（DraftStore：teacher/draft/accepted）。
- M5-c：第三视觉态渲染源描述（RENDER_SOURCES：4 种 source 视觉可区分）。
- 自适应渲染开关（ADAPTIVE_RENDER_MODES + default_render_mode）。
- 修正传播（propagate_correction：本层修正 → 相邻层空白处，不越类）。
- M5-d：推理调度（InferenceScheduler：version 去重 + 取消；merge_prompts；
  local_roi 局部遮罩）。
- 异常驱动队列（risk_queue：逐类 Dice + 多教师一致性 flag → 审阅顺序）。
- 血缘 payload（build_provenance：M5-e 建 MaskVersion 行时复用本结构；
  本模块只产 payload，不写 DB）。

诚实边界：RENDER_SOURCES 的“虚线轮廓 / 实色轮廓”在 niivue 0.53.1 的
colormapLabel LUT 里只能表达为 fill_alpha + 颜色 + 角标（LUT 按 label 值
着色，没有描边通道）；真正的逐像素轮廓线需第二 overlay 通道，见 M5-REPORT
§3.3。前端用 alpha + 角标 + 类别行虚线边框实现“视觉可区分”，不谎称
LUT 画出了虚线。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

# ---------------------------------------------------------------------------
# M5-a：编辑模式
# ---------------------------------------------------------------------------

EDIT_MODES = ("browse", "prompt_pos", "prompt_neg", "bbox", "scribble", "paint", "erase")

# 键盘映射（M5-PLAN §2.1 的 + - & * 是符号映射；键盘上 P/N/B/S 为可达键，
# UI 上同时标注符号，避免 Shift+数字难按）。
MODE_KEYS: dict[str, str] = {
    "p": "prompt_pos",
    "n": "prompt_neg",
    "b": "bbox",
    "s": "scribble",
}
MODE_SYMBOLS: dict[str, str] = {
    "browse": "·",
    "prompt_pos": "+",
    "prompt_neg": "-",
    "bbox": "&",
    "scribble": "*",
    "paint": "✎",
    "erase": "⌫",
}
MODE_LABELS: dict[str, str] = {
    "browse": "浏览",
    "prompt_pos": "正向提示点 (+)",
    "prompt_neg": "负向提示点 (-)",
    "bbox": "包围框 (&)",
    "scribble": "涂鸦 (*)",
    "paint": "画笔粗修",
    "erase": "整类删除",
}

PROMPT_TYPES = ("point", "bbox", "scribble")


def validate_prompt(prompt: dict[str, Any]) -> tuple[bool, str]:
    """一条提示是否可重放（坐标完整、数值、类型合法）。"""
    ptype = prompt.get("type")
    if ptype not in PROMPT_TYPES:
        return False, f"未知提示类型：{ptype!r}"
    if ptype == "point":
        for k in ("x", "y", "z"):
            if k not in prompt or not isinstance(prompt[k], (int, float)):
                return False, f"point 提示缺数值坐标 {k}"
        if "include" not in prompt or not isinstance(prompt["include"], bool):
            return False, "point 提示缺 include（bool）"
    elif ptype == "bbox":
        bb = prompt.get("bbox")
        if not isinstance(bb, (list, tuple)) or len(bb) != 3 or any(len(pair) != 2 for pair in bb):
            return False, "bbox 提示须为 [[x0,x1],[y0,y1],[z0,z1]]"
    elif ptype == "scribble":
        pts = prompt.get("points")
        if not isinstance(pts, list) or not pts or any(len(p) != 3 for p in pts):
            return False, "scribble 提示须为非空 [[x,y,z], ...]"
    if "class" not in prompt or not prompt["class"]:
        return False, "提示缺 class（所属类别）"
    return True, "ok"


@dataclass
class PromptSession:
    """一次审阅会话的提示状态：模式 + 每类提示列表 + 空格平移标志。

    不变量（M5-a 验收）：
    - 切模式不丢已有提示（set_mode 只改 mode）。
    - Esc 只回 browse，不清提示（esc() 不碰 prompts）。
    - 空格拖拽不落提示（space_held 时 click_allowed() 为 False）。
    """

    mode: str = "browse"
    space_held: bool = False
    prompts_by_class: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def set_mode(self, mode: str) -> None:
        if mode not in EDIT_MODES:
            raise ValueError(f"未知编辑模式：{mode!r}")
        self.mode = mode

    def key_press(self, key: str) -> str | None:
        """键盘输入 → 新模式（None = 无模式变化）。Esc 回 browse，提示保留。"""
        k = key.lower()
        if k == "escape":
            self.mode = "browse"
            return "browse"
        if k in MODE_KEYS:
            self.mode = MODE_KEYS[k]
            return self.mode
        return None

    def set_space_held(self, held: bool) -> None:
        self.space_held = held

    def click_allowed(self) -> bool:
        """当前点击是否可落提示：浏览模式不落、空格平移时不落。"""
        return self.mode != "browse" and not self.space_held

    def add_prompt(self, cls: str, prompt: dict[str, Any]) -> None:
        ok, reason = validate_prompt({**prompt, "class": cls})
        if not ok:
            raise ValueError(f"非法提示：{reason}")
        stored = {**prompt, "class": cls}
        self.prompts_by_class.setdefault(cls, []).append(stored)

    def prompts_for(self, cls: str) -> list[dict[str, Any]]:
        return list(self.prompts_by_class.get(cls, []))

    def all_prompts(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for cls in self.prompts_by_class:
            out.extend(self.prompts_by_class[cls])
        return out

    def clear_prompts(self, cls: str | None = None) -> None:
        if cls is None:
            self.prompts_by_class.clear()
        else:
            self.prompts_by_class.pop(cls, None)


# ---------------------------------------------------------------------------
# M5-b：per-class 草稿生命周期
# ---------------------------------------------------------------------------

DRAFT_STATES = ("teacher", "draft", "accepted")


def build_provenance(
    session_id: str,
    baseline_teacher_version: int | None,
    prompts: list[dict[str, Any]],
    accepted_classes: list[str],
) -> dict[str, Any]:
    """provenance（M5-PLAN §4 / M5-e）：{sessionId, baselineTeacherVersion,
    prompts[], acceptedClasses[]}。prompts 全量、可重放。"""
    for p in prompts:
        ok, reason = validate_prompt(p)
        if not ok:
            raise ValueError(f"provenance 含不可重放提示：{reason}（{p!r}）")
    return {
        "sessionId": session_id,
        "baselineTeacherVersion": baseline_teacher_version,
        "prompts": [dict(p) for p in prompts],
        "acceptedClasses": list(accepted_classes),
    }


@dataclass
class DraftStore:
    """per-class 草稿：teacher（未动）/ draft（会话草稿）/ accepted（已确认）。

    - accept 单类不影响其他类；discard 后该类回 teacher 且提示标记清空
      （调用方同时清 PromptSession 对应类的 prompts，本类只清自身状态）。
    - submit_payload() 只产出可提交的 payload + 校验，不写 DB
      （建 MaskVersion 行是 M5-e 的事，复用同一 payload 结构）。
    """

    states: dict[str, str] = field(default_factory=dict)
    accepted_order: list[str] = field(default_factory=list)

    def state_of(self, cls: str) -> str:
        return self.states.get(cls, "teacher")

    def update_draft(self, cls: str, n_prompts: int) -> None:
        if n_prompts <= 0:
            raise ValueError("draft 更新至少要 1 条提示")
        self.states[cls] = "draft"

    def discard(self, cls: str) -> str:
        """放弃本类草稿 → 回 teacher。返回放弃前的状态。"""
        prev = self.state_of(cls)
        self.states[cls] = "teacher"
        if cls in self.accepted_order:
            self.accepted_order.remove(cls)
        return prev

    def accept(self, cls: str) -> None:
        if self.state_of(cls) not in ("draft", "accepted"):
            raise ValueError(f"类 {cls!r} 无草稿可确认（当前 {self.state_of(cls)}）")
        self.states[cls] = "accepted"
        if cls not in self.accepted_order:
            self.accepted_order.append(cls)

    @property
    def has_unsubmitted(self) -> bool:
        return any(s in ("draft", "accepted") for s in self.states.values())

    @property
    def draft_classes(self) -> list[str]:
        return [c for c, s in self.states.items() if s == "draft"]

    def submit_payload(
        self,
        session: PromptSession,
        session_id: str,
        baseline_teacher_version: int | None,
    ) -> dict[str, Any]:
        """提交 payload：acceptedClasses 非空才可提交；prompts = 全部已确认类的
        全量提示（可重放）；未确认的 draft 类不进 acceptedClasses。"""
        accepted = list(self.accepted_order)
        if not accepted:
            raise ValueError("无已确认的类可提交（先确认至少一个类）")
        prompts: list[dict[str, Any]] = []
        for cls in accepted:
            prompts.extend(session.prompts_for(cls))
        if not prompts:
            raise ValueError("已确认类无提示记录，无法重放（拒绝提交空 provenance）")
        return build_provenance(session_id, baseline_teacher_version, prompts, accepted)


# ---------------------------------------------------------------------------
# M5-c：第三视觉态渲染源 + 自适应渲染开关
# ---------------------------------------------------------------------------

RENDER_SOURCES: dict[str, dict[str, Any]] = {
    # 教师预测：半透明实色（已有）。
    "teacher": {"fill_alpha": 0.6, "outline": "none", "badge": ""},
    # GT / 人工确认：亮色轮廓线（已有语言；LUT 表达为低填充 + 亮色）。
    "gt_human": {"fill_alpha": 0.15, "outline": "solid-bright", "badge": ""},
    # nnInteractive 草稿：虚线轮廓 + 低透明实色 + 角落标注。
    "guided_draft": {
        "fill_alpha": 0.25,
        "outline": "dashed-amber",
        "badge": "nnInteractive · 未确认",
    },
    # 确认后的修正：实色轮廓（与 GT 同语言）+ 小角标区分来源。
    "guided_accepted": {
        "fill_alpha": 0.6,
        "outline": "solid-cyan",
        "badge": "nnI ✓",
    },
}

ADAPTIVE_RENDER_MODES: dict[str, dict[str, Any]] = {
    # 快速定位：弱化轮廓、强化类别色块（检测/定位关键任务默认）。
    "quick": {
        "fill_alpha_scale": 1.25,
        "outline": "weak",
        "deviation_pulse": False,
        "strip_available": True,
        "note": "快速定位：弱化轮廓、强化类别色块",
    },
    # 精细边界：强化 GT 与预测边界差异，偏差区域脉动提示
    # （prefers-reduced-motion 时前端用静态高亮代替闪烁）。
    "fine": {
        "fill_alpha_scale": 0.75,
        "outline": "strong",
        "deviation_pulse": True,
        "strip_available": True,
        "note": "精细边界：强化 GT 与预测边界差异，偏差区域脉动提示",
    },
}


def default_render_mode(modality: str | None, task_kind: str | None = None) -> str:
    """默认审阅模式（M5-PLAN §1.2）：边界关键任务（肿瘤、器官精细分割）
    默认精细边界；检测/定位关键任务（未来 X-ray 接入时）默认快速定位 +
    边缘 + 低透明叠加。task_kind 显式指定时优先于 modality。"""
    if task_kind == "detection":
        return "quick"
    if task_kind == "boundary":
        return "fine"
    if (modality or "").strip().upper() in ("XR", "X-RAY", "XRAY", "DX", "CR"):
        return "quick"
    return "fine"  # CT / MRI / 未知：默认精细边界


# ---------------------------------------------------------------------------
# 修正传播：本层修正 → 相邻层（M5-PLAN §3.1，最优先）
# ---------------------------------------------------------------------------


def propagate_correction(
    arr: np.ndarray,
    z: int,
    label: int,
    radius: int = 1,
    corrected: np.ndarray | None = None,
) -> tuple[np.ndarray, int, dict[int, int]]:
    """把 z 层上某类的修正传播到相邻 radius 层。

    - arr：(x, y, z) 多标签 mask（nibabel 顺序），不原地修改，返回新数组。
    - corrected：(x, y) bool，本层被修正的体素；None = 本层该类全部体素。
    - 写规则：只写目标层上为背景（0）且落在 corrected 投影范围内的体素
      （精确复制本层修正形态，不膨胀——避免逐层外扩）；绝不覆盖其他已有
      类别体素（越类保护）。
    - 返回 (new_arr, 传播总 voxel 数，{层号: 该层传播数})。
    """
    if arr.ndim != 3:
        raise ValueError(f"mask 须为 3 维，实得 {arr.ndim} 维")
    if not (0 <= z < arr.shape[2]):
        raise ValueError(f"切片 z={z} 越界（0..{arr.shape[2] - 1}）")
    if label <= 0:
        raise ValueError(f"label 须 > 0，实得 {label}")
    if radius < 0:
        raise ValueError(f"radius 须 >= 0，实得 {radius}")
    if corrected is None:
        corrected = arr[:, :, z] == label
    if corrected.shape != arr[:, :, z].shape:
        raise ValueError(f"corrected 形状 {corrected.shape} 与切片不符")
    if not corrected.any():
        return arr.copy(), 0, {}

    new_arr = arr.copy()
    footprint = np.asarray(corrected, dtype=bool)
    per_slice: dict[int, int] = {}
    total = 0
    for d in range(1, radius + 1):
        for zz in (z - d, z + d):
            if not (0 <= zz < arr.shape[2]):
                continue
            target = new_arr[:, :, zz]
            writable = (target == 0) & footprint
            n = int(writable.sum())
            if n:
                target[writable] = label
                per_slice[zz] = n
                total += n
    return new_arr, total, per_slice


# ---------------------------------------------------------------------------
# M5-d：推理调度（AbortController + version 去重 + 局部遮罩 + 提示合并）
# ---------------------------------------------------------------------------


def merge_prompts(prompts: list[dict[str, Any]], tol: int = 1) -> list[dict[str, Any]]:
    """提示合并：同类同向 point 提示在 tol 体素内重复的只留第一条；
    bbox / scribble 不合并（形状语义不同）。保持原顺序。"""
    kept: list[dict[str, Any]] = []
    seen_points: list[tuple[Any, bool, int, int, int]] = []
    for p in prompts:
        if p.get("type") != "point":
            kept.append(p)
            continue
        key = (p.get("class"), bool(p.get("include")), int(p["x"]), int(p["y"]), int(p["z"]))
        dup = any(
            s[0] == key[0]
            and s[1] == key[1]
            and abs(s[2] - key[2]) <= tol
            and abs(s[3] - key[3]) <= tol
            and abs(s[4] - key[4]) <= tol
            for s in seen_points
        )
        if not dup:
            seen_points.append(key)
            kept.append(p)
    return kept


def local_roi(
    prompt: dict[str, Any], shape: tuple[int, int, int], margin: int = 8
) -> tuple[int, int, int, int, int, int]:
    """局部遮罩 ROI：提示周围 margin 体素的包围盒，裁剪到 volume 形状。
    返回 (x0, x1, y0, y1, z0, z1)，半开区间。"""
    if prompt.get("type") == "bbox":
        (x0, x1), (y0, y1), (z0, z1) = prompt["bbox"]
    elif prompt.get("type") == "scribble":
        pts = prompt["points"]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        zs = [p[2] for p in pts]
        x0, x1, y0, y1, z0, z1 = min(xs), max(xs) + 1, min(ys), max(ys) + 1, min(zs), max(zs) + 1
    else:  # point
        x0 = x1 = int(prompt["x"])
        y0 = y1 = int(prompt["y"])
        z0 = z1 = int(prompt["z"])
        x1, y1, z1 = x0 + 1, y0 + 1, z0 + 1
    x0 = max(0, x0 - margin)
    y0 = max(0, y0 - margin)
    z0 = max(0, z0 - margin)
    x1 = min(shape[0], x1 + margin)
    y1 = min(shape[1], y1 + margin)
    z1 = min(shape[2], z1 + margin)
    return (x0, x1, y0, y1, z0, z1)


@dataclass
class InferenceScheduler:
    """推理调度：version 递增；只有最新 version 的结果被接受；
    cancel(version) 后该 version 的结果到达即丢弃（AbortController 等价语义）。
    前端：快速连点只发最新请求；推理中仍可平移/切切片（异步，不锁 UI）。"""

    latest_version: int = 0
    cancelled: set[int] = field(default_factory=set)

    def submit(self) -> int:
        self.latest_version += 1
        return self.latest_version

    def cancel(self, version: int) -> None:
        self.cancelled.add(version)

    def cancel_latest(self) -> None:
        self.cancel(self.latest_version)

    def complete(self, version: int, result: Any) -> tuple[bool, Any]:
        """结果到达：最新且未取消 → (True, result) 接受；否则 (False, None) 丢弃。"""
        if version in self.cancelled or version != self.latest_version:
            return False, None
        return True, result


# ---------------------------------------------------------------------------
# 异常驱动队列：逐类 Dice + 多教师一致性 flag → 审阅顺序
# ---------------------------------------------------------------------------

HIGH_DICE_THRESHOLD = 0.80
MEDIUM_DICE_THRESHOLD = 0.90


def risk_queue(
    per_class_dice: dict[str, float],
    consistency_flags: Any = None,
) -> list[dict[str, Any]]:
    """审阅顺序队列：多教师一致性 flag 优先，其次逐类 Dice 升序。

    - consistency_flags：阶段 5 数据——set/list（flagged 类名）或
      dict（类名 -> bool / {"flagged": bool}）。
    - high = consistency flagged 或 dice < 0.80；medium = 0.80–0.90；
      low = > 0.90。排序：high 在前（flagged 先于单纯低 Dice），同级按
      dice 升序，同 dice 按类名。
    """
    flagged: set[str] = set()
    if isinstance(consistency_flags, dict):
        for k, v in consistency_flags.items():
            if isinstance(v, dict):
                if v.get("flagged"):
                    flagged.add(k)
            elif v:
                flagged.add(k)
    elif consistency_flags:
        flagged.update(str(c) for c in consistency_flags)

    entries: list[dict[str, Any]] = []
    for cls, dice in per_class_dice.items():
        d = float(dice)
        is_flagged = cls in flagged
        if is_flagged or d < HIGH_DICE_THRESHOLD:
            risk = "high"
            reason = (
                "多教师一致性 flag（教师 vs 学生分歧大）"
                if is_flagged
                else f"Dice {d:.4f} < {HIGH_DICE_THRESHOLD}"
            )
            if is_flagged and d < HIGH_DICE_THRESHOLD:
                reason = f"多教师一致性 flag + Dice {d:.4f} < {HIGH_DICE_THRESHOLD}"
        elif d < MEDIUM_DICE_THRESHOLD:
            risk = "medium"
            reason = f"Dice {d:.4f} 在 {HIGH_DICE_THRESHOLD}–{MEDIUM_DICE_THRESHOLD}"
        else:
            risk = "low"
            reason = f"Dice {d:.4f} > {MEDIUM_DICE_THRESHOLD}"
        entries.append(
            {
                "class": cls,
                "dice": round(d, 4),
                "risk": risk,
                "flagged": is_flagged,
                "reason": reason,
            }
        )
    rank = {"high": 0, "medium": 1, "low": 2}
    entries.sort(key=lambda e: (rank[e["risk"]], 0 if e["flagged"] else 1, e["dice"], e["class"]))
    return entries


__all__ = [
    "ADAPTIVE_RENDER_MODES",
    "DRAFT_STATES",
    "EDIT_MODES",
    "HIGH_DICE_THRESHOLD",
    "MEDIUM_DICE_THRESHOLD",
    "MODE_KEYS",
    "MODE_LABELS",
    "MODE_SYMBOLS",
    "PROMPT_TYPES",
    "RENDER_SOURCES",
    "DraftStore",
    "InferenceScheduler",
    "PromptSession",
    "build_provenance",
    "default_render_mode",
    "local_roi",
    "merge_prompts",
    "propagate_correction",
    "risk_queue",
    "validate_prompt",
]
