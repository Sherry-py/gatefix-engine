"""Tests for honest boundary documentation in README (REVISION_BRIEF.md
任务 6). Lightweight prose checks — not trying to fully parse Markdown, just
guarding against the two concrete regressions the brief calls out: the
Non-goals section disappearing, and an unqualified "已兼容/已对接" claim
about a harness whose integration protocol was never actually verified.
"""

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

README = (BASE_DIR / "README.md").read_text(encoding="utf-8")


def test_non_goals_section_exists():
    assert "## 这不是什么" in README


def test_deepseek_harness_relationship_section_exists_with_hedged_language():
    assert "与 DeepSeek Harness 等的关系" in README
    # 必须带"预留/待适配"这类限定语，不能是干净的"已兼容"陈述
    section_start = README.index("与 DeepSeek Harness 等的关系")
    section = README[section_start:section_start + 1500]
    assert "接口已预留" in section or "待协议公开" in section


def test_no_unqualified_deepseek_harness_compatibility_claim():
    """"兼容 DeepSeek Harness"这几个字如果连着出现且前面没有"没有/未/待/
    预留"这类否定或限定词，就是在暗示已经对接过，而这从没被验证过。"""
    idx = README.find("兼容")
    while idx != -1:
        window = README[max(0, idx - 30):idx]
        if "DeepSeek" in README[idx:idx + 40] or "Harness" in README[idx:idx + 40]:
            assert any(hedge in window for hedge in
                       ("没有", "未", "待", "预留", "不宣称", "不")), (
                f"unqualified compatibility claim near: {README[max(0, idx-30):idx+40]!r}"
            )
        idx = README.find("兼容", idx + 1)


def test_security_boundary_section_states_no_credentials_needed():
    assert "## 安全边界" in README
    assert "不需要、也不接收任何凭据" in README
