"""Cloud-only PDF partial delivery: source-only copies, no data/credentials/API."""
from copy import deepcopy
from pathlib import Path
import os
import shutil
import subprocess
import sys

import pytest

from mathbank.pdf_partial import complete_boundary_drafts, merge_partial_drafts, merge_recovered_questions


PAGE = "1. 第一题的内容，计算$1+1$。\n2. 已知函数$f(x)=x^2$，求$f(2)$。\n3. 第三题起点，计算$3+3$。"


def drafts(text=PAGE, page=1):
    return complete_boundary_drafts([{"page_number": page, "markdown": text}],
        source_sha256="a" * 64, paper_title="隔离测试")


def test_only_closed_interior_region_keeps_exact_source_and_pending_metadata():
    cards, isolated = drafts()
    assert len(cards) == 1
    card = cards[0]
    assert card["content"] == PAGE[PAGE.index("2."):PAGE.index("3.")]
    assert card["answer_markdown"] == "" and card["knowledge_points"] == []
    assert card["question_type"] == "" and card["difficulty"] is None
    assert card["source_review"]["required"] is True
    assert card["source_review"]["blocking"] is False
    assert card["source_review"]["disposition"] == "advisory"
    assert card["source_review"].get("verified_by") != "vision"
    assert len(isolated) == 2
    assert merge_partial_drafts(cards, cards) == cards


@pytest.mark.parametrize("replacement", [
    "2. 已知$x=1，求$x$。\n",  # unmatched math
    "2. 求值。\\begin{choices}\\item 甲\\item 乙\n",  # open choices
    "2. 求值。\\begin{tabular}{cc}a&b\\\\\n",  # open table
    "2. 依据上题求$f(2)$。\n",
    "2. 如图，求$f(2)$。\n",
    "2. [插图待补: 图1]，求值。\n",
])
def test_ambiguous_math_choices_tables_references_and_figures_are_isolated(replacement):
    text = PAGE[:PAGE.index("2.")] + replacement + PAGE[PAGE.index("3."):]
    cards, isolated = drafts(text)
    assert cards == [] and isolated


def test_never_join_across_failed_or_unselected_pages_and_skip_first_last_fragments():
    pages = [{"page_number": 1, "markdown": "1. 完整起点。\n2. 只有开头$"},
             {"page_number": 2, "markdown": ""},
             {"page_number": 3, "markdown": "x$的续文。\n3. 新题开头。"}]
    cards, isolated = complete_boundary_drafts(pages, source_sha256="b" * 64, paper_title="测试")
    assert cards == []
    assert any(row["pages"] == [2] and row["reason"] == "page_not_transcribed" for row in isolated)


def test_shared_material_and_nonconsecutive_numbers_are_isolated():
    assert drafts("以下题目共用条件。\n" + PAGE)[0] == []
    assert drafts(PAGE.replace("3.", "4."))[0] == []


def test_original_answer_is_preserved_and_not_guessed():
    text = PAGE.replace("\n3.", "\n【答案】$4$\n3.")
    card = drafts(text)[0][0]
    assert card["answer_markdown"] == "\n【答案】$4$\n"
    assert card["content"] + card["answer_markdown"] == text[text.index("2."):text.index("3.")]


def test_recovery_keeps_delivered_cards_and_only_adds_unique_source_identities():
    old = drafts()[0]
    incoming = [{"content": old[0]["content"], "answer_markdown": "模型不能覆盖旧原文"},
                {"content": "第三题起点，计算$3+3$。", "answer_markdown": ""}]
    result, isolated = merge_recovered_questions(old, incoming, PAGE, source_sha256="a" * 64)
    assert result[0] == old[0] and len(result) == 2
    assert len({q["pdf_source_card_id"] for q in result}) == 2
    assert isolated == []


def test_repeated_question_numbers_cannot_silently_absorb_a_new_section():
    old = drafts()[0]
    full = PAGE + "\n二、另一节\n2. 另一道不同题，求$9+9$。\n"
    incoming = [{"content": "另一道不同题，求$9+9$。", "answer_markdown": ""}]
    result, isolated = merge_recovered_questions(old, incoming, full, source_sha256="a" * 64)
    assert result == old
    assert isolated and isolated[0]["reason"] in {
        "recovered_source_number_not_unique", "recovered_card_identity_uncertain"}
