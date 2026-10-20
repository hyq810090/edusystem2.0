"""Real PDF/Word/LaTeX acceptance for full-width stem + options/right figures."""
from pathlib import Path
import io,json,time,zipfile,os
import httpx,fitz
from lxml import etree
from PIL import Image,ImageDraw
root=Path(__file__).resolve().parents[1];out=root/'test-results';data=out/'choices-right-docker-data'
pw=(data/'initial-admin.txt').read_text(encoding='utf-8').splitlines()[1].split('：',1)[1]
with httpx.Client(base_url='http://127.0.0.1:3011',trust_env=False,timeout=180) as c:
 c.post('/platform/api/login',json={'username':'admin','password':pw});c.headers['X-Local-Token']=c.get('/platform/api/me').json()['csrf']
 metadata=json.loads((root/'resources/textbooks/高中物理人教版.metadata.json').read_text(encoding='utf-8'));book=c.post('/platform/api/admin/textbooks',json={'subject':'物理','stage':'高中','name':'选项配图验收','metadata':metadata}).json()['id']
 sid=c.post('/platform/api/spaces',json={'name':'选项分栏导出 '+str(time.time_ns()),'textbook_id':book}).json()['id'];lane=sid;base=f'/bank/{lane}'
 im=Image.new('RGB',(280,230),'white');d=ImageDraw.Draw(im);d.line((70,70,150,15,150,170),fill='black',width=3);d.ellipse((55,60,75,80),outline='black',width=2);d.rectangle((30,170,220,185),outline='black',width=2);d.ellipse((55,185,80,210),outline='black',width=2);d.ellipse((170,185,195,210),outline='black',width=2);d.line((10,213,245,213),fill='black',width=2)
 buf=io.BytesIO();im.save(buf,format='PNG');image=c.post(base+'/api/upload',files={'file':('cart.png',buf.getvalue(),'image/png')}).json()['file_path']
 content=r'如图所示，固定在小车上的支架的斜杆与竖直杆的夹角为 $\theta$，在斜杆下端固定有质量为 $m$ 的小球。下列关于杆对球的作用力 $F$ 的判断中，正确的是（　）'+'\n'+r'\begin{choices}\item 小车静止时，$F=mg\sin\theta$，方向沿杆向上\item 小车静止时，$F=mg\cos\theta$，方向垂直于杆向上\item 小车向右匀加速运动时，一定有 $F=\frac{ma}{\sin\theta}$\item 小车向右匀速运动时，$F=mg$，方向竖直向上\end{choices}'+'\n![]('+image+')'
 if os.environ.get('CHOICES_FIGURE_BEFORE') == '1':
  content=content.replace('\n![]('+image+')','').replace(r'\begin{choices}', '\n![]('+image+')\n'+r'\begin{choices}')
 q=c.post(base+'/api/questions',data={'content':content,'image_paths':json.dumps([image]),'question_type':'single_choice','difficulty':'normal','figure_align':'choices_right','figure_align_custom':'true','figure_size':'large'}).json()['question'];qid=q['id']
 pid=c.post(base+'/api/paper/save',json={'title':'选项右侧排版验收','paper_type':'exam','questions':[{'id':qid,'score':5}]}).json()['paper_id']
 c.post(base+f'/api/questions/{qid}/figure_layout',data={'figure_align':'center','figure_size':'small'})
 q=c.get(base+f'/api/papers/{pid}').json()['data']['questions'][0]['question'];assert q['figure_align']=='choices_right'
 payload={'title':'选项右侧排版验收','paper_type':'exam','questions':[dict(q,score=5)],'show_notice':False,'show_secret':False};report={}
 for kind in ['tex','word','pdf']:
  r=c.post(base+'/api/paper/export/'+kind,json=payload);assert r.is_success,r.text[:1000];(out/f'choices-right-export.{"pdf" if kind=="pdf" else kind+".zip"}').write_bytes(r.content);report[kind]={'bytes':len(r.content)}
  if kind=='tex':
   with zipfile.ZipFile(io.BytesIO(r.content)) as z:tex='\n'.join(z.read(n).decode('utf-8') for n in z.namelist() if n.endswith('.tex'))
   assert r'\begin{choices}[columns=1]' in tex and r'{0.32\linewidth}' in tex
  if kind=='word':
   with zipfile.ZipFile(io.BytesIO(r.content)) as z:doc=z.read(next(n for n in z.namelist() if n.endswith('.docx')))
   with zipfile.ZipFile(io.BytesIO(doc)) as z:xml=etree.fromstring(z.read('word/document.xml'))
   ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main','a':'http://schemas.openxmlformats.org/drawingml/2006/main','m':'http://schemas.openxmlformats.org/officeDocument/2006/math'}
   tables=xml.xpath('//w:tbl[w:tr/w:tc[2]//a:blip]',namespaces=ns);assert len(tables)==1
   left,right=tables[0].xpath('./w:tr/w:tc',namespaces=ns)
   assert len(left.xpath('./w:p',namespaces=ns))==4
   assert left.xpath('.//m:oMath',namespaces=ns) and right.xpath('.//a:blip',namespaces=ns)
   report[kind]['option_paragraphs']=4
  if kind=='pdf':
   doc=fitz.open(stream=r.content,filetype='pdf');page=doc[0];images=page.get_images();assert len(images)==1
   rect=page.get_image_rects(images[0][0])[0];words=page.get_text('words');a=next(w for w in words if w[4] in ['A.','A．'])
   assert rect.x0>a[2] and abs(rect.y0-a[1])<45,(rect,a)
   report[kind]['image_rect']=list(rect);report[kind]['option_a']=list(a[:4]);report[kind]['pages']=len(doc)
   page.get_pixmap(matrix=fitz.Matrix(1.5,1.5)).save(out/'choices-right-export.png')
 (out/'choices-right-export-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(report))
