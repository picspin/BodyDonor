"""解压保护（zip 炸弹防护，M1，proposal §5）+ 100MB 上传流式计数。

解压保护三限（config extract_limits，proposal §5）：

1. **压缩比 > max_compression_ratio（默认 100×）拒收**
2. **嵌套 zip 深度 > max_nested_zip_depth（默认 3）拒收**
3. **文件数 > max_file_count（默认 10k）拒收**

zip 炸弹三 case 负测试各一条（tests/test_zip_bomb.py）：
高压缩比（>100×）、嵌套 zip（>3 层）、超多文件（>10k）。

100 MB 限制三处强制之一（仅 cloud_demo，proposal §4.3/§5）：
后端流式计数 —— 读流时分块累加，超过 max_upload_mb 即中断（不整包读入内存）。
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

MAX_READ_CHUNK = 1 << 20  # 1 MiB


class ExtractLimitError(Exception):
    """解压保护触发：压缩比 / 嵌套深度 / 文件数超限。"""

    def __init__(self, reason: str, limit: str):
        self.reason = reason
        self.limit = limit
        super().__init__(f"extract limit exceeded [{limit}]: {reason}")


class UploadLimitError(Exception):
    """上传体积超限（仅 cloud_demo；proposal §4.3）。"""


@dataclass
class ExtractLimits:
    max_compression_ratio: int = 100
    max_nested_zip_depth: int = 3
    max_file_count: int = 10_000

    @classmethod
    def from_settings(cls, s) -> ExtractLimits:  # noqa: ANN001 — 避免循环 import
        el = s.extract_limits
        return cls(
            max_compression_ratio=el.max_compression_ratio,
            max_nested_zip_depth=el.max_nested_zip_depth,
            max_file_count=el.max_file_count,
        )


def check_compression_ratios(zf: zipfile.ZipFile, limits: ExtractLimits) -> None:
    """逐成员检查压缩比：file_size / compress_size > max_compression_ratio 拒收。

    以任一成员超限为准（整体平均会稀释单成员炸弹；目录条目跳过）。
    """
    for info in zf.infolist():
        if info.is_dir() or info.compress_size == 0:
            continue
        ratio = info.file_size / info.compress_size
        if ratio > limits.max_compression_ratio:
            raise ExtractLimitError(
                f"member {info.filename!r} compression ratio {ratio:.0f}x "
                f"({info.file_size}B -> {info.compress_size}B)",
                limit=f"max_compression_ratio={limits.max_compression_ratio}",
            )


def check_file_count(zf: zipfile.ZipFile, limits: ExtractLimits) -> None:
    n = len(zf.infolist())
    if n > limits.max_file_count:
        raise ExtractLimitError(
            f"zip contains {n} entries (limit {limits.max_file_count})",
            limit=f"max_file_count={limits.max_file_count}",
        )


def count_nested_depth(zf: zipfile.ZipFile, max_depth: int) -> int:
    """返回 zip 内嵌套 zip 的实际层数（0 = 无嵌套）。

    深度定义：外层 zip 自身不算，内层 zip 是第 1 层，内层的内层是第 2 层……
    逐层读内层 zip 成员（每层受内存防护：条目数已由 file_count 预检限住；
    层数到 max_depth 即提前返回，不继续展开——避免炸弹放大）。
    """
    depth = 0
    current = zf
    while depth < max_depth:
        nested = [i for i in current.infolist() if i.filename.lower().endswith(".zip")]
        if not nested:
            break
        depth += 1
        if depth >= max_depth:
            break
        # 继续往里看一层
        try:
            current = zipfile.ZipFile(io.BytesIO(current.read(nested[0])))
        except (zipfile.BadZipFile, OSError, RuntimeError):
            break
    return depth


def safe_open_zip(src: BinaryIO | Path, limits: ExtractLimits) -> zipfile.ZipFile:
    """打开 zip 并跑三限预检（压缩比 / 文件数 / 嵌套深度）。

    预检不过即 raise ExtractLimitError，调用方不会拿到炸弹 zip。
    返回打开的 ZipFile（调用方负责 close）。
    """
    zf = zipfile.ZipFile(src)
    check_compression_ratios(zf, limits)
    check_file_count(zf, limits)
    depth = count_nested_depth(zf, limits.max_nested_zip_depth + 1)
    if depth > limits.max_nested_zip_depth:
        raise ExtractLimitError(
            f"zip contains {depth} nested zip members (limit {limits.max_nested_zip_depth})",
            limit=f"max_nested_zip_depth={limits.max_nested_zip_depth}",
        )
    return zf


def iter_zip_members(zf: zipfile.ZipFile, limits: ExtractLimits) -> zipfile.ZipFile:
    """已过 safe_open_zip 的 ZipFile 直接返回（预检在打开时完成）。"""
    return zf


def read_upload_stream(
    stream: BinaryIO, max_upload_mb: int | None, chunk_size: int = MAX_READ_CHUNK
) -> bytes:
    """后端流式计数（100MB 限制三处之一）：分块读流，超限即中断。

    max_upload_mb=None（on_prem）不限制。返回全量字节。
    上层（FastAPI 端点 / CLI）传 UploadFile.file 或 request.stream 包装。
    """
    if max_upload_mb is None:
        return stream.read()
    max_bytes = max_upload_mb * MAX_READ_CHUNK
    buf = io.BytesIO()
    total = 0
    while True:
        chunk = stream.read(chunk_size)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise UploadLimitError(
                f"upload exceeds {max_upload_mb} MB (streamed {total} bytes)"
            )
        buf.write(chunk)
    return buf.getvalue()
