"""Actual Linux PDF/Word/TeX exports; test container/data only, no AI calls."""
from pathlib import Path
import io,json,zipfile
import httpx
import fitz

root=Path(__file__).resolve().parents[1];out=root/'test-results'
pw=(out/'physics-docker-data/initial-admin.txt').read_text(encoding='utf-8').splitlines()[1].split('：',1)[1]
with httpx.Client(base_url='http://127.0.0.1:3011',trust_env=False,timeout=180) as c:
    assert c.post('/platform/api/login',json={'username':'admin','password':pw}).is_success
    c.headers['X-Local-Token']=c.get('/platform/api/me').json()['csrf']
    metadata=json.loads((root/'resources/textbooks/高中物理人教版.metadata.json').read_text(encoding='utf-8'))
    b=c.post('/platform/api/admin/textbooks',json={'stage':'高中','subject':'物理','name':'物理真实导出验收','metadata':metadata});assert b.is_success
    sid=c.post('/platform/api/spaces',json={'name':'物理真实导出验收','textbook_id':b.json()['id']}).json()['id']
    lane=sid
    questions=[]
    for kind,content,answer in [
        ('experiment',r'用伏安法测电阻。电压 $U=2.00\,\mathrm{V}$，电流 $I=20.0\,\mathrm{mA}$。画出电流方向，说明电表连接方式与误差来源。',r'$R=100\,\Omega$。电流表串联，电压表并联；注意量程、极性和有效数字。'),
        ('detailed_answer',r'物体质量 $m=2.0\,\mathrm{kg}$，水平合力 $F=4.0\,\mathrm{N}$，求加速度并说明方向。',r'$a=2.0\,\mathrm{m/s^2}$，方向与合力相同。'),
        ('short_answer','说明串联电路与并联电路的电流关系。','串联电路各处电流相等；并联电路干路电流等于各支路电流之和。')]:
        r=c.post(f'/bank/{lane}/api/questions',data={'content':content,'question_type':kind,'difficulty':'normal','answer_markdown':answer})
        assert r.is_success,r.text;questions.append(dict(r.json()['question'],score=10))
    payload={'title':'物理学科适配验收','paper_type':'exam','questions':questions,'include_answers':True,'show_secret':False,'show_notice':False}
    report={}
    for kind in ('tex','word','pdf'):
        r=c.post(f'/bank/{lane}/api/paper/export/{kind}',json=payload)
        assert r.status_code==200,r.text[:800]
        (out/f'physics-export-{kind}.{ "pdf" if kind=="pdf" else "zip"}').write_bytes(r.content)
        report[kind]={'status':r.status_code,'bytes':len(r.content)}
        if kind=='tex':
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:tex='\n'.join(z.read(n).decode('utf-8') for n in z.namelist() if n.endswith('.tex'))
            assert all(v in tex for v in (r'\subject{物理}','实验题','计算题','简答题','20.0','mA'))
        if kind=='word':
            with zipfile.ZipFile(io.BytesIO(r.content)) as outer:
                for name in outer.namelist():
                    if not name.endswith('.docx'):continue
                    with zipfile.ZipFile(io.BytesIO(outer.read(name))) as doc:
                        xml=doc.read('word/document.xml').decode('utf-8')
                        assert 'oMath' in xml and '物  理' in xml and '数  学' not in xml
                        assert all(v in xml for v in ('实验题','计算题','简答题'))
        if kind=='pdf':
            doc=fitz.open(stream=r.content,filetype='pdf');text=''.join(page.get_text() for page in doc)
            assert '物理' in text.replace(' ','') and all(v in text for v in ('实验题','计算题','简答题'))
            assert '数学' not in text.replace(' ','')
            report[kind]['pages']=len(doc);doc[0].get_pixmap(matrix=fitz.Matrix(1.4,1.4)).save(out/'physics-export-pdf.png')
    (out/'physics-export-report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report))
