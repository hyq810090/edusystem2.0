"""Export independent A/B layouts with the real Linux PDF/Word/TeX engines."""
from pathlib import Path
import io,json,zipfile,time
import httpx,fitz
from PIL import Image,ImageDraw
from lxml import etree
root=Path(__file__).resolve().parents[1];out=root/'test-results';data=out/'paper-layout-docker-data'
pw=(data/'initial-admin.txt').read_text(encoding='utf-8').splitlines()[1].split('：',1)[1]
with httpx.Client(base_url='http://127.0.0.1:3011',trust_env=False,timeout=180) as c:
    assert c.post('/platform/api/login',json={'username':'admin','password':pw}).is_success
    c.headers['X-Local-Token']=c.get('/platform/api/me').json()['csrf']
    sid=c.post('/platform/api/spaces',json={'name':'试卷独立排版导出 '+str(time.time_ns()),'textbook_id':1}).json()['id']
    lane=sid;base=f'/bank/{lane}'
    im=Image.new('RGB',(320,160),'white');ImageDraw.Draw(im).line((30,130,160,25,290,130,30,130),fill='black',width=3)
    buf=io.BytesIO();im.save(buf,format='PNG')
    image=c.post(base+'/api/upload',files={'file':('triangle.png',buf.getvalue(),'image/png')}).json()['file_path']
    r=c.post(base+'/api/questions',data={'content':'如图，三角形底边长 $6$，高为 $4$，求它的面积。\n![]('+image+')','image_paths':json.dumps([image]),'question_type':'detailed_answer','difficulty':'normal','figure_align':'right','figure_align_custom':'true','figure_size':'small','answer_markdown':'面积为 $12$。'})
    assert r.is_success,r.text;q=r.json()['question'];qid=q['id']
    def save(title):
        r=c.post(base+'/api/paper/save',json={'title':title,'paper_type':'exam','questions':[{'id':qid,'score':10}]});assert r.is_success,r.text
        return r.json()['paper_id']
    a=save('A卷右侧小图')
    c.post(base+f'/api/questions/{qid}/figure_layout',data={'figure_align':'center','figure_size':'large'})
    b=save('B卷居中大图')
    c.post(base+f'/api/questions/{qid}/figure_layout',data={'figure_align':'bottom_left','figure_size':'medium'})
    report={}
    for label,pid in [('A',a),('B',b)]:
        paper=c.get(base+f'/api/papers/{pid}').json()['data'];question=paper['questions'][0]['question']
        payload={'title':paper['title'],'paper_type':paper['paper_type'],'questions':[dict(question,score=10)],'show_secret':False,'show_notice':False}
        report[label]={'align':question['figure_align'],'size':question['figure_size']}
        for kind in ('tex','word','pdf'):
            r=c.post(base+f'/api/paper/export/{kind}',json=payload);assert r.status_code==200,r.text[:800]
            (out/f'paper-layout-{label}-{kind}.{ "pdf" if kind=="pdf" else "zip"}').write_bytes(r.content)
            report[label][kind]={'bytes':len(r.content)}
            if kind=='tex':
                with zipfile.ZipFile(io.BytesIO(r.content)) as z:tex='\n'.join(z.read(n).decode('utf-8') for n in z.namelist() if n.endswith('.tex'))
                assert ('max width=5.0cm' in tex and r'\begin{minipage}' in tex) if label=='A' else ('width=11.0cm' in tex)
            if kind=='word':
                with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                    doc=z.read(next(n for n in z.namelist() if n.endswith('.docx')))
                with zipfile.ZipFile(io.BytesIO(doc)) as z:xml=etree.fromstring(z.read('word/document.xml'))
                ns={'wp':'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing'}
                extents=xml.xpath('//wp:extent/@cx',namespaces=ns);assert extents
                report[label][kind]['image_width']=int(extents[0])
            if kind=='pdf':
                doc=fitz.open(stream=r.content,filetype='pdf');rects=[list(rect) for page in doc for image in page.get_images() for rect in page.get_image_rects(image[0])]
                assert rects;report[label][kind]['image_rects']=rects
                doc[0].get_pixmap(matrix=fitz.Matrix(1.2,1.2)).save(out/f'paper-layout-{label}.png')
    assert report['A']['word']['image_width']<report['B']['word']['image_width']
    ar=report['A']['pdf']['image_rects'][0];br=report['B']['pdf']['image_rects'][0]
    assert ar[0]>br[0] and ar[2]-ar[0]<br[2]-br[0],report
    (out/'paper-layout-export-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))
