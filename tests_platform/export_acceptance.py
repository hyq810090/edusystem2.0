"""Exercise the real compiler and Word exporter; never call a paid model."""
from pathlib import Path
import io
import json
import os
import zipfile

import fitz
import httpx

root=Path(__file__).resolve().parents[1]
data=root/'test-results/docker-data'
password=(data/'initial-admin.txt').read_text(encoding='utf-8').splitlines()[1].split('：',1)[1]
with httpx.Client(base_url='http://127.0.0.1:3011',trust_env=False,timeout=180) as c:
    assert c.post('/platform/api/login',json={'username':'admin','password':password}).status_code==200
    c.headers['X-Local-Token']=c.get('/platform/api/me').json()['csrf']
    assert c.get('/workbench/1').status_code==200
    result=c.post('/bank/1/api/questions',data={'content':r'已知函数 $f(x)=x^2+2x$，求 $f\prime(x)$。','question_type':'detailed_answer','difficulty':'normal','answer_markdown':r'$f\prime(x)=2x+2$。'})
    assert result.status_code==200,result.text
    question=result.json()['question']
    payload={'title':'MathBank 原版导出验收','paper_type':'exam','questions':[dict(question,score=12)],'show_secret':False,'show_notice':False,'include_answers':True}
    report={}
    for kind in ('tex','word','pdf'):
        response=c.post('/bank/1/api/paper/export/'+kind,json=payload)
        report[kind]={'status':response.status_code,'bytes':len(response.content)}
        if response.status_code!=200:report[kind]['error']=response.json()
        else:
            suffix='pdf' if kind=='pdf' else 'zip'
            (root/'test-results'/f'native-export-{kind}.{suffix}').write_bytes(response.content)
            if kind=='word':
                with zipfile.ZipFile(io.BytesIO(response.content)) as outer:
                    assert len(outer.namelist())==2
                    with zipfile.ZipFile(io.BytesIO(outer.read(outer.namelist()[0]))) as inner:
                        assert b'oMath' in inner.read('word/document.xml')
            if kind=='pdf':
                document=fitz.open(stream=response.content,filetype='pdf')
                report[kind]['pages']=len(document)
                assert len(document)>0
                document[0].get_pixmap(matrix=fitz.Matrix(1.2,1.2)).save(root/'test-results/native-export-pdf.png')
    (root/'test-results/export-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))
    assert all(r['status']==200 for r in report.values())
