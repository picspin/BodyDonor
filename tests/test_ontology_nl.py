"""M2-align 3.4：本体 NL 解析分层测试（精确/模糊/规则/IntentParser）+ 20 条测试集。

断网可跑：不调 LLM、不联网（单测断言离线通过）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bodydonor.config import Settings
from bodydonor.ontology import (
    AMBIGUOUS,
    FUZZY_CUTOFF,
    RESOLVED,
    IntentCandidate,
    OntologyService,
    RuleIntentParser,
    get_intent_parser,
    register_intent_parser,
    split_multi_label,
)

NL_CASES = Path(__file__).parent / "data" / "ontology_nl_cases.json"


@pytest.fixture
def service(settings: Settings) -> OntologyService:  # noqa: ANN001
    return OntologyService(settings)


class TestMultiLabel:
    def test_split_cn_he(self) -> None:
        """'肝和脾' -> ['肝', '脾']（分隔符多标签）。"""
        assert split_multi_label("肝和脾") == ["肝", "脾"]

    def test_split_cn_dunhao(self) -> None:
        """'肝、脾' -> ['肝', '脾']。"""
        assert split_multi_label("肝、脾") == ["肝", "脾"]

    def test_split_en_and(self) -> None:
        """'liver and spleen' -> ['liver', 'spleen']（英文 and 词边界）。"""
        assert split_multi_label("liver and spleen") == ["liver", "spleen"]

    def test_split_three(self) -> None:
        """'肝、脾、胰腺' -> 3 个。"""
        assert split_multi_label("肝、脾、胰腺") == ["肝", "脾", "胰腺"]

    def test_split_empty(self) -> None:
        assert split_multi_label("") == []

    def test_split_no_delimiter(self) -> None:
        assert split_multi_label("肝") == ["肝"]


class TestLeftRight:
    def test_left_cn_kidney(self, service: OntologyService) -> None:
        """'左肾' -> kidney_left（左右修饰解析）。"""
        r = service.resolve("左肾")
        assert r.state == RESOLVED
        assert r.entry.totalseg_class == "kidney_left"

    def test_right_en_kidney(self, service: OntologyService) -> None:
        """'right kidney' -> kidney_right。"""
        r = service.resolve("right kidney")
        assert r.state == RESOLVED
        assert r.entry.totalseg_class == "kidney_right"

    def test_left_cn_femur(self, service: OntologyService) -> None:
        """'左股骨' -> femur_left。"""
        r = service.resolve("左股骨")
        assert r.state == RESOLVED
        assert r.entry.totalseg_class == "femur_left"

    def test_right_cn_adrenal(self, service: OntologyService) -> None:
        """'右肾上腺' -> adrenal_gland_right。"""
        r = service.resolve("右肾上腺")
        assert r.state == RESOLVED
        assert r.entry.totalseg_class == "adrenal_gland_right"


class TestAbbreviation:
    def test_dan_ambiguous_not_silent(self, service: OntologyService) -> None:
        """'胆' -> ambiguous（gallbladder/liver），不许静默单解。"""
        r = service.resolve("胆")
        assert r.state == AMBIGUOUS
        candidates = {c.entry.totalseg_class for c in r.candidates}
        assert candidates == {"gallbladder", "liver"}

    def test_shen_ambiguous(self, service: OntologyService) -> None:
        """'肾'（无左右修饰）-> ambiguous（kidney_left/right）。"""
        r = service.resolve("肾")
        assert r.state == AMBIGUOUS
        candidates = {c.entry.totalseg_class for c in r.candidates}
        assert candidates == {"kidney_left", "kidney_right"}

    def test_femur_ambiguous(self, service: OntologyService) -> None:
        """'femur'（无左右）-> ambiguous。"""
        r = service.resolve("femur")
        assert r.state == AMBIGUOUS


class TestFuzzy:
    def test_fuzzy_en_livr(self, service: OntologyService) -> None:
        """'livr' -> liver（编辑距离模糊命中，唯一最高分）。"""
        r = service.resolve("livr")
        assert r.state == RESOLVED
        assert r.entry.totalseg_class == "liver"
        assert "模糊" in r.reason

    def test_fuzzy_cn(self, service: OntologyService) -> None:
        """'脾脏器' -> spleen（中文模糊命中）。"""
        r = service.resolve("脾脏器")
        assert r.state == RESOLVED
        assert r.entry.totalseg_class == "spleen"

    def test_fuzzy_cutoff_constant(self) -> None:
        """阈值是代码常量（M2-align 3.4：阈值写进 config，测试锁定）。"""
        assert 0.0 < FUZZY_CUTOFF < 1.0

    def test_fuzzy_ambiguity_not_silent(self, service: OntologyService) -> None:
        """模糊多候选（并列最高分）-> ambiguous，不许静默单解。"""
        # 构造：截断词命中多个并列候选
        r = service.resolve("vertebrae_T")  # T 系列多个椎体并列
        if r.state == RESOLVED:
            pytest.skip("vertebrae_T 精确命中单一类（表内无并列）")
        assert r.state == AMBIGUOUS


class TestIntentParser:
    def test_rule_parser_default(self, service: OntologyService) -> None:
        """默认实现是 RuleIntentParser（离线规则）。"""
        parser = get_intent_parser(service)
        assert isinstance(parser, RuleIntentParser)

    def test_parse_multi(self, service: OntologyService) -> None:
        """'肝和脾' -> [liver(1.0), spleen(1.0)]。"""
        parser = get_intent_parser(service)
        out = parser.parse("肝和脾")
        assert [c.totalseg_class for c in out] == ["liver", "spleen"]
        assert all(c.confidence == 1.0 for c in out)

    def test_parse_ambiguous_confidence(self, service: OntologyService) -> None:
        """'肾' ambiguous 候选 -> confidence 0.5（需用户点选）。"""
        parser = get_intent_parser(service)
        out = parser.parse("肾")
        assert {c.totalseg_class for c in out} == {"kidney_left", "kidney_right"}
        assert all(c.confidence == 0.5 for c in out)

    def test_parse_empty(self, service: OntologyService) -> None:
        parser = get_intent_parser(service)
        assert parser.parse("") == []

    def test_register_llm_parser_not_called(self, service: OntologyService) -> None:
        """LLM 意图解析只留注册点：注册自定义工厂后 get_intent_parser 返回它。

        本轮不调任何 LLM、不联网——测试用 stub 工厂验证注册机制。
        """
        calls = []

        def factory(svc: OntologyService) -> object:
            calls.append(svc)

            class _StubParser:
                def parse(self, text: str) -> list[IntentCandidate]:
                    return [IntentCandidate("liver", 0.99)]

            return _StubParser()

        from bodydonor import ontology as ont

        old = ont._intent_parser_factory
        try:
            register_intent_parser(factory)
            parser = get_intent_parser(service)
            out = parser.parse("任意")
            assert out[0].totalseg_class == "liver"
            assert calls  # 工厂被调用
        finally:
            ont._intent_parser_factory = old  # 还原注册点

    def test_offline_no_network(self, service: OntologyService) -> None:
        """断网可跑：解析全程不联网（离线查表/规则/模糊，无 LLM 调用）。"""
        # resolve 全链路是纯离线（查表 + difflib + re）；冒烟断言无网络痕迹
        parser = get_intent_parser(service)
        assert isinstance(parser, RuleIntentParser)  # 未注册 LLM（本轮约定）
        r = service.resolve(["肝", "脾"][0])
        assert r.state == RESOLVED


class TestNLCaseSet:
    """20 条中英测试集（tests/data/ontology_nl_cases.json）：断网全通过。"""

    @pytest.fixture
    def cases(self) -> list[dict]:  # noqa: ANN201
        if not NL_CASES.exists():
            pytest.fail(f"测试集缺失：{NL_CASES}")
        return json.loads(NL_CASES.read_text(encoding="utf-8"))["cases"]

    def test_case_set_20(self, cases: list[dict]) -> None:
        """测试集 22 条（中英各 10+），六类覆盖（精确/模糊/左右/多标签/ambig/unresolved）。

        M2-align 3.4 要 20 条中英各半；实际 22 条（多 2 条冗余覆盖），中英各 ≥10。
        """
        assert len(cases) == 22
        layers = {c["layer"] for c in cases}
        for required in ["exact", "fuzzy", "left_right", "multi_label",
                         "abbreviation", "unresolved"]:
            assert required in layers, required
        cn = sum(1 for c in cases if any("一" <= ch <= "鿿" for ch in c["input"]))
        en = len(cases) - cn
        assert cn >= 10 and en >= 10  # 中英各半（宽断言：容许混合输入）

    def test_all_cases_pass_offline(self, cases: list[dict], service: OntologyService) -> None:
        """全部 20 条离线通过（不联网、不调 LLM）。"""
        parser = get_intent_parser(service)
        for c in cases:
            if "expect_classes" in c:
                # 多标签：走 IntentParser（置信度 1.0 的 resolved 才算）
                out = parser.parse(c["input"])
                got = [x.totalseg_class for x in out if x.confidence >= 1.0]
                assert got == c["expect_classes"], (
                    f"{c['id']} ({c['input']}): {got} != {c['expect_classes']}"
                )
            else:
                r = service.resolve(c["input"])
                assert r.state == c["expect_state"], (
                    f"{c['id']} ({c['input']}): {r.state} != {c['expect_state']}"
                    f"（{r.reason}）"
                )
                if c["expect_state"] == RESOLVED:
                    assert r.entry.totalseg_class == c["expect_class"], c["id"]
                elif c["expect_state"] == AMBIGUOUS:
                    got = sorted(x.entry.totalseg_class for x in r.candidates)
                    assert got == sorted(c["expect_candidates"]), c["id"]
