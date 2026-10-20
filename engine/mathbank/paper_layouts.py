"""Paper-owned figure layouts, independent of mutable question-bank defaults."""
import json
import sqlite3
import time
from pathlib import Path

from .image_layout import normalize_image_layouts

KEY = "question_figure_layouts_v1"
ALIGNS = {"right", "bottom_left", "center", "bottom_right", "choices_right"}
SIZES = {"auto", "small", "medium", "large"}


def snapshot(question, overrides=None, paper_type="exam"):
    values = dict(question)
    overrides = overrides or {}
    for key in ("figure_align", "figure_align_custom", "figure_size", "image_layouts"):
        if key in overrides:
            values[key] = overrides[key]
    align = values.get("figure_align") or "right"
    size = values.get("figure_size") or "auto"
    custom = values.get("figure_align_custom", False)
    if align not in ALIGNS or size not in SIZES:
        raise ValueError("无效的试卷插图位置或尺寸")
    if not isinstance(custom, (bool, int)) or custom not in (True, False):
        raise ValueError("无效的试卷插图排版标志")
    if align == "right" and not custom and paper_type == "quiz":
        align = "bottom_right"
    return {
        "figure_align": align,
        # Freeze the effective position even when the template changes later.
        "figure_align_custom": True,
        "figure_size": size,
        "image_layouts": normalize_image_layouts(values.get("image_layouts") or {}, values.get("content", "")),
    }


def paper_question(question, metadata, paper_type):
    result = dict(question)
    layouts = metadata.get(KEY, {})
    layout = layouts.get(str(question["id"]), {}) if isinstance(layouts, dict) else {}
    result.update(snapshot(question, layout, paper_type))
    return result


def migrate_legacy_papers(engine):
    """Freeze existing papers before requests can change their question defaults.

    This is a metadata-only, idempotent migration; the upstream schema stays intact.
    A SQLite online backup is mandatory for persistent databases with legacy papers.
    """
    from .paths import SCHEMA_SNAPSHOT_DIR
    with engine.connect() as conn:
        rows = conn.exec_driver_sql("SELECT id, metadata_json FROM papers").mappings().all()
        legacy_ids = []
        for row in rows:
            meta = json.loads(row["metadata_json"] or "{}")
            if not isinstance(meta, dict):
                raise ValueError("旧试卷元数据格式无效，已停止排版迁移")
            if KEY not in meta:
                legacy_ids.append(row["id"])
    if not legacy_ids:
        return 0
    database = engine.url.database
    if database and database != ":memory:":
        directory = Path(SCHEMA_SNAPSHOT_DIR)
        directory.mkdir(parents=True, exist_ok=True)
        backup = directory / f"before-paper-layouts-{time.time_ns()}.db"
        with sqlite3.connect(database) as source, sqlite3.connect(backup) as destination:
            source.backup(destination)
        backup.chmod(0o600)
    with engine.begin() as conn:
        for paper_id in legacy_ids:
            paper = conn.exec_driver_sql("SELECT paper_type, metadata_json FROM papers WHERE id=?", (paper_id,)).mappings().one()
            meta = json.loads(paper["metadata_json"] or "{}")
            if KEY in meta:
                continue
            questions = conn.exec_driver_sql(
                "SELECT q.* FROM questions q JOIN paper_questions pq ON pq.question_id=q.id WHERE pq.paper_id=?",
                (paper_id,),
            ).mappings().all()
            meta[KEY] = {str(q["id"]): snapshot(q, paper_type=paper["paper_type"]) for q in questions}
            conn.exec_driver_sql("UPDATE papers SET metadata_json=? WHERE id=?", (json.dumps(meta, ensure_ascii=False), paper_id))
    return len(legacy_ids)
