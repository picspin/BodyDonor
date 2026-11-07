#!/usr/bin/env python3
"""TotalSegmentator dataset（CT-Lite CC-BY-4.0 镜像）单 member 下载器（M3，遗留 2(f)）。

背景（M1 已记录）：TS dataset 全量 28.4 GB（Zenodo 6802614）/ 22.6 GB（HF
TotalSegmentator-CT-Lite Images.zip），本机单流 ~1 MB/s，全量 >8h，M1 决定
「QC 冷启动延后至 M3 前」。M3 冷启动校准 + hold-out 只需 ~30-60 例，
全量下载不必要——本脚本用 **zip 范围请求**只抓需要的 case：

方法（实测验证）：
1. HF `resolve` 302 -> xet-bridge CDN（`us.aws.cdn.hf.co`），该 CDN 支持 Range
   （Zenodo 也支持 Range 但对 >10KB 的 CD 抓取截断到 ~27KB，不可用）。
2. 抓 zip 末尾 EOCD（最后 ~64KB，覆盖 zip64 EOCD）-> 解析 central directory
   偏移；CD 分块抓取（每块 20KB，逐块校验长度，不齐重试一次）。
3. CD 逐条解析（local header offset + csize + crc）-> 按
   `[lho, lho+30+nlen+elen+csize)` Range 抓取单 member -> 跳过 local header
   -> raw deflate（zlib -15）解压 -> CRC32 校验。

注意：zip member 是 **raw deflate**，不是 gzip 包装（先踩过这坑）；
nii.gz member 解压后才是 gzip 流。

用法：
    uv run python scripts/dl_ct_lite.py --list                 # 列出全部 case
    uv run python scripts/dl_ct_lite.py --cases s0338,s1394    # 下载指定 case
    uv run python scripts/dl_ct_lite.py --limit 40 --split train  # 前 40 个 train case
"""

from __future__ import annotations

import argparse
import binascii
import json
import struct
import sys
import urllib.request
import zlib
from pathlib import Path

BASE = "https://huggingface.co/datasets/YongchengYAO/TotalSegmentator-CT-Lite/resolve/main"
IMAGES_ZIP = f"{BASE}/Images.zip"
MASKS_ZIP = f"{BASE}/Masks.zip"
CD_CHUNK = 20000  # CD 分块大小（字节）：实测 >27KB 单块会被 CDN 截断（Zenodo 同病）
# zip64 EOCD 56B + locator 20 + EOCD 22B + 4B 解包余量（64KB 整窗会把 EOCD 截掉 2 字节）
EOCD_WINDOW = 65640


def _final_url(url: str) -> str:
    """resolve 302 -> 最终 CDN URL（xet-bridge 签名链接）。"""
    req = urllib.request.Request(url, method="HEAD")
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.url


def _range(url: str, start: int, end: int, retries: int = 2) -> bytes:
    """Range 抓取 [start, end]（含），逐次校验长度；不齐重试，不猜结果。"""
    expect = end - start + 1
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers={"Range": f"bytes={start}-{end}"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                data = r.read()
        except OSError as e:
            if attempt == retries:
                raise RuntimeError(f"Range 抓取失败 [{start},{end}]：{e}") from e
            continue
        if len(data) == expect:
            return data
        if attempt == retries:
            raise RuntimeError(
                f"Range 长度不符 [{start},{end}]：got {len(data)} expect {expect}"
            )
    raise RuntimeError("unreachable")


def fetch_cd(zip_url: str, total_size: int) -> bytes:
    """抓 central directory（EOCD 定位 -> 分块抓取逐块校验）。"""
    url = _final_url(zip_url)
    tail_start = max(0, total_size - EOCD_WINDOW)
    tail = _range(url, tail_start, total_size - 1)
    i = tail.rfind(b"PK\x06\x06")  # zip64 EOCD
    if i >= 0:
        _sz, _v1, _v2, _d1, _d2, _n1, _n2, cd_size, cd_offset = struct.unpack(
            "<QHHIIQQQQ", tail[i + 4 : i + 4 + 52]
        )
    else:
        j = tail.rfind(b"PK\x05\x06")
        if j < 0:
            raise RuntimeError(f"zip 无 EOCD：{zip_url}")
        # EOCD: sig(4) disk(2) cddisk(2) n_this(2) n_total(2) cd_size(4) cd_offset(4) = 16 字节体
        _disk, _cd, _n1, _n2, cd_size, cd_offset = struct.unpack(
            "<HHHHII", tail[j + 4 : j + 4 + 16]
        )
    out = bytearray()
    pos = 0
    while pos < cd_size:
        start = cd_offset + pos
        end = cd_offset + min(pos + CD_CHUNK, cd_size) - 1
        out += _range(url, start, end)
        pos += CD_CHUNK
    return bytes(out)


def parse_cd(cd: bytes) -> list[dict]:
    """CD 逐条解析（name/csize/usize/method/lho/crc）。"""
    entries = []
    i = 0
    while i < len(cd) - 46 and cd[i : i + 4] == b"PK\x01\x02":
        (
            _sig, _ver, _ver2, _flags, method, _mt, _md, crc, csize, usize,
            nlen, elen, clen, _disk, _iattr, _eattr, lho,
        ) = struct.unpack("<IHHHHHHIIIHHHHHII", cd[i : i + 46])
        name = cd[i + 46 : i + 46 + nlen].decode("utf-8")
        entries.append(
            {"name": name, "csize": csize, "usize": usize, "method": method,
             "lho": lho, "crc": crc}
        )
        i += 46 + nlen + elen + clen
    return entries


def extract_member(zip_url: str, e: dict) -> bytes:
    """单 member 抓取 + raw deflate 解压 + CRC 校验（不校验失败写盘）。"""
    url = _final_url(zip_url)
    lho, csize, crc = e["lho"], e["csize"], e["crc"]
    raw_member = _range(url, lho, lho + 30 + 260 + csize - 1)
    nlen = struct.unpack("<H", raw_member[26:28])[0]
    elen = struct.unpack("<H", raw_member[28:30])[0]
    data_start = 30 + nlen + elen
    payload = raw_member[data_start : data_start + csize]
    if e["method"] == 0:
        data = payload
    else:
        # zip member 是 raw deflate（zlib -15），不是 gzip 包装
        data = zlib.decompress(payload, -15)
    actual = binascii.crc32(data) & 0xFFFFFFFF
    if actual != crc:
        raise RuntimeError(f"CRC 不符：{e['name']} want {crc:#x} got {actual:#x}")
    return data


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--list", action="store_true", help="列出全部 case 后退出")
    ap.add_argument("--cases", default="", help="逗号分隔 case id（如 s0338,s1394）")
    ap.add_argument("--limit", type=int, default=0, help="取前 N 个 train case")
    ap.add_argument("--out", default=str(Path.home() / "data/BodyDonor/downloads/ct_lite"))
    args = ap.parse_args()

    # zip 总大小：HEAD content-length（resolve 302 链）
    req = urllib.request.Request(IMAGES_ZIP, method="HEAD")
    with urllib.request.urlopen(req, timeout=30) as r:
        total = int(r.headers["Content-Length"])
    print(f"[dl_ct_lite] Images.zip total {total/1e9:.1f} GB（不全量下载，按 member 抓）")

    cd = fetch_cd(IMAGES_ZIP, total)
    entries = parse_cd(cd)
    images = [e for e in entries if e["name"].endswith(".nii.gz")]
    print(f"[dl_ct_lite] CD entries={len(entries)} images={len(images)}")

    if args.list:
        for e in images:
            print(e["name"])
        return 0

    wanted = images
    if args.cases:
        ids = {c.strip() for c in args.cases.split(",") if c.strip()}
        wanted = [e for e in images if any(c in e["name"] for c in ids)]
    if args.limit:
        wanted = wanted[: args.limit]
    if not wanted:
        print("no cases selected")
        return 1

    # masks CD（同一镜像 Masks.zip）
    req = urllib.request.Request(MASKS_ZIP, method="HEAD")
    with urllib.request.urlopen(req, timeout=30) as r:
        m_total = int(r.headers["Content-Length"])
    mcd = fetch_cd(MASKS_ZIP, m_total)
    mask_entries = parse_cd(mcd)

    out_dir = Path(args.out)
    (out_dir / "images").mkdir(parents=True, exist_ok=True)
    (out_dir / "masks").mkdir(parents=True, exist_ok=True)
    manifest = []
    for e in wanted:
        case_id = e["name"].split("/")[-1].removesuffix(".nii.gz")
        me = next(
            (m for m in mask_entries if m["name"].endswith(f"/{case_id}.nii.gz")), None
        )
        if me is None:
            print(f"[dl_ct_lite] SKIP {case_id}（Masks.zip 无对应 mask）")
            continue
        img_path = out_dir / "images" / f"{case_id}.nii.gz"
        msk_path = out_dir / "masks" / f"{case_id}.nii.gz"
        if img_path.exists() and msk_path.exists():
            manifest.append({
                "case_id": case_id, "image": str(img_path),
                "mask": str(msk_path), "cached": True,
            })
            continue
        img_path.write_bytes(extract_member(IMAGES_ZIP, e))
        msk_path.write_bytes(extract_member(MASKS_ZIP, me))
        manifest.append({
            "case_id": case_id, "image": str(img_path),
            "mask": str(msk_path), "cached": False,
        })
        print(f"[dl_ct_lite] {case_id}: image {e['csize']/1e6:.1f}MB mask {me['csize']/1e6:.1f}MB")

    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    print(f"[dl_ct_lite] 完成 {len(manifest)} cases -> {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
