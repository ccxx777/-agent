"""中英文Query Specificity与通用Prompt的确定性测试。"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.agent.prompts import ANSWER_PROMPT, CHAT_SYSTEM_PROMPT
from app.services import query_specificity as specificity


def test_english_keyword_query_keeps_literal_weight() -> None:
    with patch.object(specificity, "detect_query_language", return_value=("en", 0.99)):
        result = specificity.calculate_query_specificity_details(
            "learning rate scheduler"
        )
    assert result.signal_density == 0.0
    assert result.specificity == 0.8


def test_english_natural_question_uses_function_word_density() -> None:
    with patch.object(specificity, "detect_query_language", return_value=("en", 0.99)):
        result = specificity.calculate_query_specificity_details(
            "What is the learning rate?"
        )
    assert result.total_tokens == 5
    assert result.signal_tokens == 3
    assert result.signal_density == pytest.approx(0.6)
    assert result.specificity == pytest.approx(0.44)


def test_chinese_content_verbs_are_not_social_fillers() -> None:
    with patch.object(specificity, "detect_query_language", return_value=("zh", 0.99)):
        keyword = specificity.calculate_query_specificity_details("中期考核细则")
        natural = specificity.calculate_query_specificity_details(
            "关于中期考核的办理流程"
        )
    assert keyword.specificity == 0.8
    assert natural.signal_tokens >= 2  # 关于、的
    assert 0.45 <= natural.specificity <= 0.7


def test_uncertain_or_short_query_uses_neutral_weight() -> None:
    with patch.object(
        specificity,
        "detect_query_language",
        return_value=("fallback", 0.2),
    ):
        result = specificity.calculate_query_specificity_details("规定")
    assert result.specificity == 0.5
    assert result.semantic_weight == 0.5
    assert result.literal_weight == 0.5


def test_prompts_are_generic_and_require_supported_partial_answers() -> None:
    assert "华中科技大学" not in ANSWER_PROMPT
    assert "通用知识库助手" in ANSWER_PROMPT
    assert "部分答案" in ANSWER_PROMPT
    assert "search_knowledge_base" in CHAT_SYSTEM_PROMPT
