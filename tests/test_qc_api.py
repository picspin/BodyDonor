"""QC API 端点 + 决策回流单测（M3）：评分 / 抽检任务 / 决策回流。"""

from __future__ import annotations

import pytest
import yaml
from fastapi.testclient import TestClient

from bodydonor.app import app
from bodydonor.qc import QC_THRESHOLDS_PATH
from bodydonor.qc_reflux import record_review_decision


@pytest.fixture()
def client():
    return TestClient(app)


def test_thresholds_endpoint(client):
    r = client.get("/qc/thresholds")
    assert r.status_code == 200
    doc = r.json()
    assert "rules" in doc
    assert "review_sampling" in doc
    # M4 起 multi_teacher_consistency 启用（学生模型已出现，阶段 5）
    assert doc["rules"]["multi_teacher_consistency"]["enabled"] is True
    assert doc["rules"]["multi_teacher_consistency"]["threshold"] == 0.80


def test_review_tasks_list_endpoint(client):
    r = client.get("/qc/review-tasks")
    assert r.status_code == 200
    assert "tasks" in r.json()


def test_review_task_decision_endpoint_404(client):
    r = client.post("/qc/review-tasks/rt-nonexistent/decision", params={"decision": "accepted"})
    assert r.status_code == 404


def test_review_task_decision_invalid(client):
    r = client.post(
        "/qc/review-tasks/rt-anything/decision", params={"decision": "maybe"}
    )
    assert r.status_code == 422


def test_record_review_decision_updates_thresholds(tmp_path):
    """决策回流：累计 calibrated_on/at + decisions 计数（真实 yaml 写回）。"""
    import shutil

    p = tmp_path / "qc_thresholds.yaml"
    shutil.copy(QC_THRESHOLDS_PATH, p)
    out1 = record_review_decision(None, "accepted", path=p)
    assert out1["calibrated_on"] == 1
    assert out1["decisions"]["accepted"] == 1
    out2 = record_review_decision(None, "rejected", path=p)
    assert out2["calibrated_on"] == 2
    assert out2["decisions"]["rejected"] == 1
    doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert doc["overall"]["calibrated_on"] == 2
    assert doc["overall"]["decisions"]["accepted"] == 1
    # 每个阈值的 source 注记同步更新（proposal §7：报告可见）
    for cfg in doc["rules"].values():
        assert cfg["calibrated_on"] == 2
        assert cfg["calibrated_at"]


def test_record_review_decision_invalid():
    with pytest.raises(ValueError, match="decision"):
        record_review_decision(None, "maybe")


def test_record_review_decision_preserves_development_provenance(tmp_path):
    """回流只动反馈计数，不碰开发 provenance（P2：正常回流曾把
    holdout_30_dev/30 覆盖成 holdout_30_dev/1，开发依据被抹掉）。"""
    import shutil

    p = tmp_path / "qc_thresholds.yaml"
    shutil.copy(QC_THRESHOLDS_PATH, p)
    before = yaml.safe_load(p.read_text(encoding="utf-8"))
    mt_before = before["rules"]["multi_teacher_consistency"]["development_provenance"]
    sp_before = before["sampling_priority"]["development_provenance"]
    record_review_decision(None, "accepted", path=p)
    record_review_decision(None, "corrected", path=p)
    doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    mt = doc["rules"]["multi_teacher_consistency"]
    sp = doc["sampling_priority"]
    # 开发 provenance 逐字段不变
    assert mt["development_provenance"] == mt_before
    assert sp["development_provenance"] == sp_before
    assert mt["development_provenance"]["n_cases"] == 30
    assert mt["development_provenance"]["source"] == "holdout_30_dev"
    assert mt["development_provenance"]["case_ids_hash"] == "218a663e218d4ce7"
    # 反馈计数从 0 起正确累计（calibrated_on = 反馈数，不再冒充开发 case 数）
    assert mt["calibrated_on"] == 2
    assert sp["calibrated_on"] == 2
    assert doc["overall"]["calibrated_on"] == 2


def test_record_review_decision_old_config_without_development_provenance(tmp_path):
    """旧配置（97a3fe8，无 development_provenance）回流兼容读取：不虚构
    development_provenance，反馈计数正确累计为 1。

    不虚构一半是守卫（旧配置没有就没有），sp/overall 累计一半是
    fail-first（旧回流不更新 sampling_priority，旧代码上失败，
    assert 30 == 1）；作用是固化旧配置兼容语义，防未来回归（如回流
    时强插默认 provenance）。
    """
    import subprocess

    old = subprocess.run(
        ["git", "show", "97a3fe8:config/qc_thresholds.yaml"],
        capture_output=True, text=True, check=True,
    ).stdout
    p = tmp_path / "qc_thresholds.yaml"
    p.write_text(old, encoding="utf-8")
    record_review_decision(None, "accepted", path=p)
    doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    mt = doc["rules"]["multi_teacher_consistency"]
    sp = doc["sampling_priority"]
    # 不虚构开发 provenance（旧配置没有就没有）
    assert "development_provenance" not in mt
    assert "development_provenance" not in sp
    # 反馈计数正确累计（旧值 30 是开发 case 数，回流后归位为反馈计数 1）
    assert mt["calibrated_on"] == 1
    assert sp["calibrated_on"] == 1
    assert doc["overall"]["calibrated_on"] == 1
