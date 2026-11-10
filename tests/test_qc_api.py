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
