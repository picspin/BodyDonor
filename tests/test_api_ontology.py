"""M2：本体映射 API 端点测试（三态 / 消歧写回 / 逃生通道）。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from bodydonor.app import app


def client() -> TestClient:
    return TestClient(app)


class TestOntologyEndpoints:
    def test_resolve_resolved(self) -> None:
        """唯一命中 -> resolved，含 SNOMED/Uberon。"""
        with client() as c:
            r = c.get("/ontology/resolve", params={"label": "肝"})
        assert r.status_code == 200
        body = r.json()
        assert body["state"] == "resolved"
        assert body["entry"]["totalseg_class"] == "liver"
        assert body["entry"]["snomed_ct"]

    def test_resolve_english_synonym(self) -> None:
        """英文同义词同表命中。"""
        with client() as c:
            r = c.get("/ontology/resolve", params={"label": "spleen"})
        assert r.status_code == 200
        assert r.json()["entry"]["totalseg_class"] == "spleen"

    def test_resolve_unresolved(self) -> None:
        with client() as c:
            r = c.get("/ontology/resolve", params={"label": "不存在的器官xyz"})
        assert r.status_code == 200
        assert r.json()["state"] == "unresolved"

    def test_disambiguate_write_back(self, monkeypatch, tmp_path) -> None:  # noqa: ANN001
        """消歧写回：422 防非候选；写回走临时目录（不污染真实 overrides.yaml）。"""
        import shutil

        from bodydonor import ontology as onto

        # 复制真实 v1 表到临时目录，service 指向临时副本
        tmp_base = tmp_path / "ontology"
        shutil.copytree(onto.ONTOLOGY_DIR, tmp_base)
        monkeypatch.setattr(onto, "ONTOLOGY_DIR", tmp_base)
        with client() as c:
            # 非候选类别 -> 422
            r_bad = c.post(
                "/ontology/disambiguate",
                params={"label": "肝", "totalseg_class": "not_a_class"},
            )
            assert r_bad.status_code == 422
        # 真实表未被污染
        assert not (onto.ONTOLOGY_DIR / "v1" / "overrides.yaml").exists()

    def test_escape_hatch_disabled_by_default(self) -> None:
        """config 默认 enabled=false -> 拒绝。"""
        with client() as c:
            r = c.get("/ontology/escape-hatch", params={"label": "未知器官xyz"})
        assert r.status_code == 200
        assert r.json()["allowed"] is False
        assert r.json()["experimental"] is False

    def test_summary(self) -> None:
        with client() as c:
            r = c.get("/ontology/summary")
        assert r.status_code == 200
        body = r.json()
        assert body["version"] == "v1"
        assert body["n_entries"] > 20
        assert "experimental_text_prompt" in body
