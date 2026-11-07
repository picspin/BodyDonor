#!/usr/bin/env bash
# M2 blocker workaround: Tailscale MagicDNS (100.100.100.100) 转发持续故障，
# release-assets.githubusercontent.com / objects.githubusercontent.com 解析失败。
# curl --resolve 硬编码 GitHub 边缘 IP 绕过 DNS 下载 TotalSegmentator total 任务
# 剩余 part（292/293/294/295/298；291 已由官方下载器就位）。
# 解压复现官方 download_url_and_unpack 逻辑：zip -> extractall(config_dir)。
set -u

CONFIG_DIR="$HOME/.totalsegmentator/nnunet/results"
BASE="https://github.com/wasserth/TotalSegmentator/releases/download/v2.0.0-weights"
RESOLVE=(--resolve github.com:443:20.205.243.166
         --resolve release-assets.githubusercontent.com:443:185.199.108.133
         --resolve objects.githubusercontent.com:443:185.199.108.133)

PARTS=(
  Dataset292_TotalSegmentator_part2_vertebrae_1532subj
  Dataset293_TotalSegmentator_part3_cardiac_1559subj
  Dataset294_TotalSegmentator_part4_muscles_1559subj
  Dataset295_TotalSegmentator_part5_ribs_1559subj
  Dataset298_TotalSegmentator_total_6mm_1559subj
)

for part in "${PARTS[@]}"; do
  # 官方逻辑：解压后目录 = config_dir/<part>/...；checkpoint 在即跳过
  if find "$CONFIG_DIR/$part" -name "checkpoint_final.pth" 2>/dev/null | grep -q .; then
    echo "[skip] $part 已就位"
    continue
  fi
  zip_path="$CONFIG_DIR/${part}.zip"
  echo "[dl] $part"
  for try in 1 2 3; do
    if curl -sL --fail --retry 2 --max-time 1800 "${RESOLVE[@]}" \
        -o "$zip_path" "$BASE/${part}.zip"; then
      echo "[unzip] $part"
      ~/work/BodyDonor/.venv-totalseg/bin/python -c "
import zipfile, sys
with zipfile.ZipFile(sys.argv[1]) as z:
    z.extractall(sys.argv[2])
" "$zip_path" "$CONFIG_DIR" && rm -f "$zip_path" && break
    fi
    echo "[retry] $part attempt $try failed"
    sleep 10
  done
  if find "$CONFIG_DIR/$part" -name "checkpoint_final.pth" 2>/dev/null | grep -q .; then
    echo "[ok] $part checkpoint 落位"
  else
    echo "[FAIL] $part"
    exit 1
  fi
done

echo "===ALL_PARTS_DONE==="
find "$CONFIG_DIR" -name "checkpoint_final.pth" | sort
