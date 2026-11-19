"""回归守卫：deep-reviewer.md 必须含"验证器审查"（区域四）及其六个核查项关键词。

防止后续改动悄悄删区。断言的是工作树版 agent 文件（演示/正式审查时注入的规则
以 main 版为准，此守卫只保证工作树版不丢区）。
"""

from pathlib import Path

AGENT = Path(__file__).resolve().parents[1] / ".claude" / "agents" / "deep-reviewer.md"


def test_agent_file_contains_validator_region():
    text = AGENT.read_text(encoding="utf-8")
    assert "## 验证器审查" in text


def test_agent_file_contains_six_checkpoint_keywords():
    text = AGENT.read_text(encoding="utf-8")
    for kw in (
        "sleep 假装异步",
        "跳过断言",
        "测试写真实路径",
        "降低阈值",
        "放宽断言",
        "mock 冒充实测",
        "M6-F 原案",
        "严重度指引",
   ):
        assert kw in text, f"missing keyword: {kw}"
