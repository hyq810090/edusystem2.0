"""北师大版 and 湘教版 高中数学 (catalogs from MathBank 3.0): offered on new and existing installs, usable as a space."""
import json
import sqlite3

from test_platform import system  # noqa: F401
from platform_app import migrations, store


def books(client):
    return {(b['stage'], b['subject'], b['name']): b for b in client.get('/platform/api/textbooks').json()}


def test_new_editions_are_offered_and_work_as_a_space(system):  # noqa: F811
    available = books(system)
    for name, version, first_chapter in (('北师大版', 'N', '第一章 预备知识'), ('湘教版', 'X', '第1章 集合与逻辑')):
        book = available[('高中', '数学', name)]
        space = system.post('/platform/api/spaces', json={'name': name + '空间', 'kind': 'personal',
                                                           'textbook_id': book['id']}).json()['id']
        tree = system.get(f'/bank/{space}/api/categories').json()
        assert list(tree) == ['必修一', '必修二', '选修一', '选修二'] and first_chapter in tree['必修一']
        section = tree['必修一'][first_chapter][0]
        created = system.post(f'/bank/{space}/api/questions', data={
            'content': f'{name}测试题：求 $1+1$。', 'question_type': 'detailed_answer', 'difficulty': '2',
            'category_compulsory': '必修一', 'category_chapter': first_chapter, 'category_knowledge': section})
        assert created.status_code == 200, created.text
        saved = created.json()['question']
        assert (saved['category_chapter'], saved['category_knowledge']) == (first_chapter, section)


def test_existing_installs_get_the_new_editions_once(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA', tmp_path)
    store.initialize()
    names = {r['name']: r['engine_version'] for r in store.rows("SELECT name,engine_version FROM textbooks WHERE stage='高中' AND subject='数学'")}
    assert names == {'人教A版': 'A', '人教B版': 'B', '苏教版': 'S', '沪教版': 'H', '北师大版': 'N', '湘教版': 'X'}
    with sqlite3.connect(tmp_path / 'platform.db') as c:
        c.execute("DELETE FROM textbooks WHERE name IN ('北师大版','湘教版')")
        c.execute("DELETE FROM platform_migrations WHERE name='senior-math-editions-v1'")
    store.initialize()
    rows = store.rows("SELECT name,engine_version,metadata FROM textbooks WHERE name IN ('北师大版','湘教版') ORDER BY engine_version")
    assert [(r['name'], r['engine_version']) for r in rows] == [('北师大版', 'N'), ('湘教版', 'X')]
    assert json.loads(rows[0]['metadata'])['grades'] == ['高一', '高二', '高三']
    assert {r['name'] for r in store.rows('SELECT name FROM platform_migrations')} == set(migrations.NAMES)
    assert len(list((tmp_path / 'migration_backups').glob('before-senior-math-editions-*.db'))) == 1
    store.initialize()
    assert store.one("SELECT count(*) AS n FROM textbooks WHERE name='北师大版'")['n'] == 1
