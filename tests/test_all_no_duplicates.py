"""main 自带两 nit 清理守卫测试（M7-CLEANUP）。"""

from __future__ import annotations

import bodydonor.distill_data as distill_data


def test_distill_data_all_no_duplicates():
    """__all__ 无重复条目（distill_data.py 曾重复 ALLOWED_TRAIN_SOURCES）。"""
    assert len(distill_data.__all__) == len(set(distill_data.__all__))
    assert distill_data.__all__.count("ALLOWED_TRAIN_SOURCES") == 1
    # 条目真实存在（拼错的导出名会让 import * 报 AttributeError）
    assert all(hasattr(distill_data, n) for n in distill_data.__all__)
