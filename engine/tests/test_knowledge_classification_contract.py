"""AI/import classifications accept only a real subsection of the selected chapter."""
import pytest

from main import validate_import_knowledge
from mathbank.prompts import build_classification_system_prompt

TREE = {"必修二": {"8. 立体几何初步": ["8.1 基本立体图形", "8.2 立体图形的直观图"]}}


def test_import_knowledge_accepts_real_leaf_or_explicit_whole_chapter():
    rows = [{"category_compulsory": "必修二", "category_chapter": "8. 立体几何初步", "category_knowledge": "8.1 基本立体图形"},
            {"category_compulsory": "必修二", "category_chapter": "8. 立体几何初步", "category_knowledge": ""}]
    validate_import_knowledge(rows, TREE)
    assert not any("classification_review" in row for row in rows)


def test_import_knowledge_aligns_name_formats_to_the_catalog():
    row = {"category_compulsory": "必修第二册", "category_chapter": "第八章 立体几何初步",
           "category_knowledge": "8.1基本立体图形"}
    diagnostics = {}
    validate_import_knowledge([row], TREE, diagnostics=diagnostics)
    assert (row["category_compulsory"], row["category_chapter"], row["category_knowledge"]) == (
        "必修二", "8. 立体几何初步", "8.1 基本立体图形")
    assert row["classification_review"]["required"] is False
    assert diagnostics["import_classification"]["normalized"] == 1
    assert diagnostics["import_classification"]["review_required"] == 0


def test_classification_uses_actual_book_then_chapter_catalog_order():
    prompt = build_classification_system_prompt({
        "必修一": {"9. 后一册章节": ["9.1 后一考点"]},
        "必修二": {"1. 前编号但后册章节": ["1.1 后一册考点"]},
    })
    assert "先比较教材册次在目录中的排列" in prompt
    assert "同一册内再比较章节在目录中的排列" in prompt
    assert "册次和章节先后只能按当前教材大纲中展示的JSON插入顺序比较" in prompt
    assert prompt.index("必修一") < prompt.index("必修二")
    assert "裸章节编号" in prompt


@pytest.mark.parametrize(("row", "kept"), [
    ({"category_compulsory": "必修二", "category_chapter": "8. 立体几何初步", "category_knowledge": "8. 立体几何初步"},
     ("必修二", "8. 立体几何初步", "")),
    ({"category_compulsory": "必修二", "category_chapter": "8. 立体几何初步", "category_knowledge": "1.1 Other-version leaf"},
     ("必修二", "8. 立体几何初步", "")),
    ({"category_compulsory": "选择性必修一", "category_chapter": "空间向量", "category_knowledge": "8.1 基本立体图形"},
     ("", "", "")),
    ({"category_compulsory": "必修二", "category_chapter": "8. 立体几何初步", "category_knowledge": None},
     ("必修二", "8. 立体几何初步", "")),
])
def test_import_knowledge_flags_chapter_labels_wrong_version_and_malformed_values_on_that_question(row, kept):
    """MathBank 3.0.0: no whole-import error; the unmatched part is cleared for the teacher to choose."""
    other = {"category_compulsory": "必修二", "category_chapter": "8. 立体几何初步", "category_knowledge": ""}
    original = dict(row)
    validate_import_knowledge([row, other], TREE)
    assert (row["category_compulsory"], row["category_chapter"], row["category_knowledge"]) == kept
    review = row["classification_review"]
    assert review["required"] is True and review["original"] == original and review["message"]
    assert "classification_review" not in other


def test_a_review_written_by_the_model_is_discarded():
    row = {"category_compulsory": "必修二", "category_chapter": "8. 立体几何初步", "category_knowledge": "",
           "classification_review": {"required": False, "confirmed_by": "user"}}
    validate_import_knowledge([row], TREE)
    assert "classification_review" not in row
