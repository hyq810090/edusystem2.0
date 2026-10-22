"""Ordered one-time upgrades of an existing platform.db.

Fresh installs are created at the current schema and record every name here
as already applied. Each step backs up whatever it changes before changing it.
"""
from collections import defaultdict
import json
import time

from platform_app import difficulty_migration, junior_math_grade9, physics_metadata, store


def single_bank_spaces():
    """Turn every former subject/stage lane of a space into a space of its own.

    The new space keeps the lane's id, so data/banks/<id>/, /workbench/<id>
    links and browser drafts stay valid. A space that had several stages is
    split into one space per stage, each with the original members and a copy
    of the paper folder tree.
    """
    probe = store.connect()
    try:
        legacy = probe.execute("SELECT 1 FROM sqlite_master WHERE name='lanes'").fetchone()
    finally:
        probe.close()
    if not legacy:
        return
    backup = store.backup('single-bank-spaces')
    c = store.connect()
    try:
        c.execute('PRAGMA foreign_keys=OFF')
        c.execute('BEGIN IMMEDIATE')
        fetch = lambda sql: [dict(r) for r in c.execute(sql).fetchall()]
        old_spaces = {s['id']: s for s in fetch('SELECT * FROM spaces')}
        lanes = fetch('SELECT * FROM lanes ORDER BY id')
        stages = defaultdict(list)
        for lane in lanes:
            stages[lane['space_id']].append(lane)
        folders = fetch('SELECT * FROM folders ORDER BY id')
        next_folder = max((f['id'] for f in folders), default=0) + 1
        folder_ids = {}
        new_spaces, new_folders, report = [], [], []
        for space_id, siblings in stages.items():
            space = old_spaces[space_id]
            tree = [f for f in folders if f['space_id'] == space_id]
            for index, lane in enumerate(siblings):
                name = space['name'] if len(siblings) == 1 else f"{space['name']}（{lane['stage']}）"
                new_spaces.append((lane['id'], name, space['kind'], space['owner_id'], space['team_id'],
                                   lane['textbook_id'], space['created']))
                for folder in tree:
                    if index == 0:
                        folder_ids[lane['id'], folder['id']] = folder['id']
                    else:
                        folder_ids[lane['id'], folder['id']] = next_folder
                        next_folder += 1
                new_folders += [(folder_ids[lane['id'], f['id']], lane['id'], f['kind'], f['name'],
                                 folder_ids.get((lane['id'], f['parent_id']))) for f in tree]
                report.append({'old_space': space_id, 'space': lane['id'], 'name': name, 'stage': lane['stage']})
        members = [(lane['id'], m['user_id'], m['permission'])
                   for m in fetch('SELECT * FROM members') for lane in stages[m['space_id']]]
        paper_folders = [(p['lane_id'], p['paper_id'], p['folder_id'] and folder_ids.get((p['lane_id'], p['folder_id'])))
                         for p in fetch('SELECT * FROM paper_folders')]
        origins = [tuple(o.values()) for o in fetch(
            'SELECT lane_id,question_id,source_lane,source_question,source_space_name,copied_by,created FROM origins')]
        tasks = [tuple(t.values()) for t in fetch('SELECT id,lane_id,user_id FROM tasks')]
        for table in ('paper_folders', 'origins', 'tasks', 'folders', 'members', 'lanes', 'spaces'):
            c.execute('DROP TABLE ' + table)
        for name in ('spaces', 'space_textbook_fixed', 'members', 'folders', 'paper_folders', 'origins', 'tasks'):
            c.execute(store.SCHEMA[name])
        c.executemany('INSERT INTO spaces(id,name,kind,owner_id,team_id,textbook_id,created) VALUES (?,?,?,?,?,?,?)', new_spaces)
        c.executemany('INSERT INTO members VALUES (?,?,?)', members)
        c.executemany('INSERT INTO folders VALUES (?,?,?,?,?)', new_folders)
        c.executemany('INSERT INTO paper_folders VALUES (?,?,?)', paper_folders)
        c.executemany('INSERT INTO origins VALUES (?,?,?,?,?,?,?)', origins)
        c.executemany('INSERT INTO tasks VALUES (?,?,?)', tasks)
        if 'last_space_id' not in {r['name'] for r in c.execute('PRAGMA table_info(users)')}:
            c.execute('ALTER TABLE users ADD COLUMN last_space_id INTEGER')
        if c.execute('PRAGMA foreign_key_check').fetchall():
            raise RuntimeError('空间拆分后外键校验失败，已回滚；原数据未改动。')
        c.commit()
    except BaseException:
        c.rollback()
        raise
    finally:
        c.close()
    dropped = sorted(set(old_spaces) - set(stages))
    backup.with_suffix('.json').write_text(json.dumps({'spaces': report, 'dropped_spaces_without_bank': dropped},
                                                      ensure_ascii=False, indent=2), encoding='utf-8')


def senior_math_editions():
    """Offer 北师大版 and 湘教版 高中数学 on existing installs; existing textbooks and spaces are untouched."""
    have = {(r["stage"], r["subject"], r["name"]) for r in store.rows("SELECT stage,subject,name FROM textbooks")}
    missing = [(v, n) for v, n in store.SENIOR_MATH_BOOKS.items() if v in ("N", "X") and ("高中", "数学", n) not in have]
    if not missing:
        return
    store.backup("senior-math-editions")
    with store.db() as c:
        c.executemany("INSERT INTO textbooks(stage,subject,name,engine_version,metadata) VALUES (?,?,?,?,?)",
                      [("高中", "数学", name, version, json.dumps(store.senior_math_metadata(version), ensure_ascii=False))
                       for version, name in missing])


def add_table(name):
    """A new table is purely additive; it is still created after a backup like every step."""
    store.backup(f'add-{name}')
    with store.db() as c:
        c.execute(store.SCHEMA[name].replace('CREATE TABLE', 'CREATE TABLE IF NOT EXISTS', 1))


STEPS = [
    ('physics-types-v1', lambda: physics_metadata.migrate(store)),
    ('single-bank-spaces-v1', single_bank_spaces),
    ('difficulty-1-5-v1', lambda: difficulty_migration.migrate(store)),
    ('junior-math-grade9-v1', lambda: junior_math_grade9.migrate(store)),
    ('handouts-v1', lambda: add_table('handouts')),
    ('drawings-v1', lambda: add_table('drawings')),
    ('senior-math-editions-v1', senior_math_editions),
]
NAMES = [name for name, _ in STEPS]


def apply():
    with store.db() as c:
        c.execute('CREATE TABLE IF NOT EXISTS platform_migrations(name TEXT PRIMARY KEY, created REAL NOT NULL)')
    done = {r['name'] for r in store.rows('SELECT name FROM platform_migrations')}
    for name, step in STEPS:
        if name not in done:
            step()
            with store.db() as c:
                c.execute('INSERT INTO platform_migrations VALUES (?,?)', (name, time.time()))
