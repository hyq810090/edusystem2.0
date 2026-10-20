import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from test_platform import system, new_space, question
from platform_app import difficulty_migration
from engine.mathbank.difficulty import fix_metadata, normalize


def test_legacy_tags_never_imply_rank():
    for code, tag in [('easy_error','易错'),('qiangji','强基'),('competition','竞赛')]:
        assert normalize(code, '真题') == ('unrated', '真题,'+tag)
    assert normalize('5', '易错,强基') == ('5','易错,强基')
    assert normalize('custom','',{'custom':'校内特训'}) == ('unrated','原难度：校内特训')
    metadata=fix_metadata({'difficulties':[{'value':'qiangji','label':'强基'}]})
    assert fix_metadata(metadata)==metadata
    assert [x['value'] for x in metadata['difficulties']]==['unrated','1','2','3','4','5']


def test_migration_backup_preservation_and_repeat(tmp_path):
    from contextlib import contextmanager
    @contextmanager
    def db():
        with sqlite3.connect(tmp_path/'platform.db') as c: yield c
    def rows(sql):
        with db() as c:
            c.row_factory=sqlite3.Row
            return [dict(row) for row in c.execute(sql)]
    with db() as c:
        c.execute('CREATE TABLE textbooks(id INTEGER,metadata TEXT)')
        c.execute('INSERT INTO textbooks VALUES(1,?)',(json.dumps({'difficulties':[{'value':'special','label':'专题'}]}),))
        c.execute('CREATE TABLE spaces(id INTEGER,textbook_id INTEGER)')
        c.execute('INSERT INTO spaces VALUES(1,1)')
    bank=tmp_path/'banks/1/math_question_bank.db';bank.parent.mkdir(parents=True)
    with sqlite3.connect(bank) as c:
        c.execute('CREATE TABLE questions(id INTEGER,difficulty TEXT,tags TEXT,content TEXT)')
        c.executemany('INSERT INTO questions VALUES(?,?,?,?)',[(1,'qiangji','真题','原文与图片'),(2,'3','易错','原文2'),(3,'special','','原文3')])
    fake=SimpleNamespace(DATA=tmp_path,rows=rows,db=db)
    difficulty_migration.migrate(fake)
    backups=list((tmp_path/'migration_backups').iterdir());assert len(backups)==1
    with sqlite3.connect(backups[0]/'bank-1.db') as c:
        assert c.execute('SELECT difficulty FROM questions WHERE id=1').fetchone()[0]=='qiangji'
    with sqlite3.connect(bank) as c:
        assert c.execute('SELECT * FROM questions').fetchall()==[(1,'unrated','真题,强基','原文与图片'),(2,'3','易错','原文2'),(3,'unrated','原难度：专题','原文3')]
    difficulty_migration.migrate(fake)
    assert len(list((tmp_path/'migration_backups').iterdir()))==1


def test_api_fixed_scale_tags_filter_and_stats(system):
    space=new_space(system,'五级难度验证')['id']
    for level in range(1,6):
        response=question(system,space,content=f'难度验证 {level}',difficulty=str(level),tags='易错,强基,竞赛')
        assert response.status_code==200,response.text
        assert response.json()['question']['difficulty']==str(level)
    response=question(system,space,content='旧格式题',difficulty='qiangji',tags='真题')
    q=response.json()['question'];assert q['difficulty']=='unrated' and q['tags']=='真题,强基'
    stats=system.get(f'/bank/{space}/api/stats').json()
    assert stats['difficulty_counts']=={str(n):1 for n in range(1,6)}|{'unrated':1}
    assert stats['normal_count']==5 and stats['qiangji_count']==6
    metadata=system.get(f'/bank/{space}/api/config/metadata').json()
    assert [v['value'] for v in metadata['difficulties']]==['unrated','1','2','3','4','5']
    metadata['difficulties']=[{'value':'competition','label':'竞赛'}]
    response=system.post('/platform/api/admin/textbooks',json={'stage':'初中','subject':'数学','name':'五级测试模板','metadata':metadata})
    assert response.status_code==200,response.text
    book=system.get(f"/platform/api/admin/textbooks/{response.json()['id']}").json()
    assert [v['value'] for v in book['metadata']['difficulties']]==['unrated','1','2','3','4','5']
