from pathlib import Path
import json
import sqlite3
import sys

from fastapi.testclient import TestClient

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine.mathbank.difficulty import fix_metadata
from platform_app import junior_math_grade9, migrations, store
from platform_app.app import app

NAME='junior-math-grade9-v1'
SEVENTH_EIGHTH=json.loads((store.ROOT/'resources/textbooks/初中数学人教版七八年级.metadata.json').read_text(encoding='utf-8'))

def pending():
    """Make the next store.initialize() run this migration again, as on an older install."""
    with store.db() as c:c.execute('DELETE FROM platform_migrations WHERE name=?',(NAME,))

def books():
    return {b['id']:b['metadata'] for b in store.rows('SELECT id,metadata FROM textbooks')}

def backups(root):
    return sorted((root/'migration_backups').glob('before-junior-math-grade9-*.db'))

def test_appends_grade9_once_with_backup_and_leaves_other_books(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'DATA',tmp_path)
    store.initialize()
    assert NAME in migrations.NAMES and not backups(tmp_path)
    metadata=fix_metadata(SEVENTH_EIGHTH)
    with_grade9=dict(metadata,curriculum={**metadata['curriculum'],'九年级上册':{'第二十四章 圆':['24.1 圆']}})
    with store.db() as c:
        add=lambda stage,subject,name,value:c.execute('INSERT INTO textbooks(stage,subject,name,metadata) VALUES (?,?,?,?)',
            (stage,subject,name,json.dumps(value,ensure_ascii=False))).lastrowid
        target=add('初中','数学','人教版',metadata)
        others=[add('初中','数学','北师大版',metadata),add('初中','数学','人教版（自定九年级）',with_grade9),
                add('初中','物理','人教版',metadata),add('初中','数学','人教版（空目录）',dict(metadata,curriculum={}))]
    before=books()
    pending()
    store.initialize()
    after=books()
    assert {i for i in before if before[i]!=after[i]}=={target}
    old,new=json.loads(before[target]),json.loads(after[target])
    assert list(new['curriculum'])==[*old['curriculum'],'九年级上册','九年级下册（暂定）']
    assert all(new['curriculum'][v]==chapters for v,chapters in old['curriculum'].items())
    assert new['curriculum']['九年级上册']['第二十九章 圆']==['29.1 圆的有关概念','29.2 圆的有关性质','29.3 弧长和扇形面积']
    assert new['grades']==[*old['grades'],'九年级']
    assert {k:v for k,v in new.items() if k not in {'curriculum','grades'}}=={k:v for k,v in old.items() if k not in {'curriculum','grades'}}
    assert all(json.loads(after[i])==json.loads(before[i]) for i in others)
    [backup]=backups(tmp_path)
    with sqlite3.connect(backup) as c:
        assert c.execute('SELECT metadata FROM textbooks WHERE id=?',(target,)).fetchone()[0]==before[target]
    report=json.loads(backup.with_suffix('.json').read_text(encoding='utf-8'))
    assert [b['id'] for b in report['textbooks']]==[target]
    assert NAME in {r['name'] for r in store.rows('SELECT name FROM platform_migrations')}
    # Registered once; rerunning the step itself (e.g. a lost marker) also changes nothing.
    store.initialize()
    junior_math_grade9.migrate(store)
    pending()
    store.initialize()
    assert books()==after and backups(tmp_path)==[backup]

def test_existing_and_new_spaces_can_classify_into_grade9(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'DATA',tmp_path)
    with TestClient(app) as client:
        password=(tmp_path/'initial-admin.txt').read_text(encoding='utf-8').splitlines()[1].split('：',1)[1]
        assert client.post('/platform/api/login',json={'username':'admin','password':password}).status_code==200
        client.headers['X-Local-Token']=client.get('/platform/api/me').json()['csrf']
        book=client.post('/platform/api/admin/textbooks',json={'stage':'初中','subject':'数学','name':'人教版','metadata':SEVENTH_EIGHTH})
        assert book.status_code==200,book.text
        def space(name):
            response=client.post('/platform/api/spaces',json={'name':name,'kind':'personal','textbook_id':book.json()['id']})
            assert response.status_code==200,response.text
            return response.json()['id']
        def save(space_id,volume,chapter,section):
            response=client.post(f'/bank/{space_id}/api/questions',data={'content':f'{chapter}：{section}','question_type':'detailed_answer',
                'difficulty':'3','category_compulsory':volume,'category_chapter':chapter,'category_knowledge':section})
            assert response.status_code==200,response.text
            return response.json()['question']['id']
        existing=space('迁移前的初中数学')
        pythagoras=save(existing,'八年级下册','第二十章 勾股定理','20.1 勾股定理')
        assert '九年级上册' not in client.get(f'/bank/{existing}/api/categories').json()
        pending()
        migrations.apply()
        # The running worker of the existing space takes the catalog on its next request.
        for space_id in (existing,space('迁移后新建的初中数学')):
            categories=client.get(f'/bank/{space_id}/api/categories').json()
            assert list(categories)[-3:]==['八年级下册','九年级上册','九年级下册（暂定）']
            assert categories['九年级上册']['第二十九章 圆']==['29.1 圆的有关概念','29.2 圆的有关性质','29.3 弧长和扇形面积']
            assert client.get(f'/bank/{space_id}/api/config/metadata').json()['grades'][-1]=='九年级'
            circle=save(space_id,'九年级上册','第二十九章 圆','29.2 圆的有关性质')
            saved=client.get(f'/bank/{space_id}/api/questions/{circle}').json()
            assert (saved['category_compulsory'],saved['category_chapter'])==('九年级上册','第二十九章 圆')
        kept=client.get(f'/bank/{existing}/api/questions/{pythagoras}').json()
        assert (kept['category_compulsory'],kept['category_chapter'],kept['category_knowledge'])==('八年级下册','第二十章 勾股定理','20.1 勾股定理')
