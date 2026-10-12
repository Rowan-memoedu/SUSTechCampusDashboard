"""Read generated PDF bytes, verify structure and source coverage, render every page."""
from pathlib import Path
import json,re,hashlib,math,sys
from pypdf import PdfReader
import pypdfium2 as pdfium
from PIL import Image,ImageOps,ImageDraw
from render_booklets import BASE,read_sections,solution_sections

# Only standalone headings delimit assignments; prose can cite other problems.
PROBLEM_HEADING=r'^Problem[ \t]+(\d+(?:\.\d+)*)[ \t]*$'
_fixture='Problem 2.3\nContinuation of Problem 8. See Problem 6.8.\nProblem 2.4\nText.'
assert re.findall(PROBLEM_HEADING,_fixture,re.M)==['2.3','2.4']

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def words(t):return re.findall(r'[a-z]+|[\u4e00-\u9fff]',t.lower())
def stem_words(t):
    t=re.sub(r'!\[[^\]]*\]\([^)]*\)','',t)
    t=re.sub(r'\$\$[\s\S]*?\$\$|\$[^$]+\$','',t)
    return words(t)
def covers(expected,actual):
    # pypdf can join inline math to prose (e.g. vector a + "and" -> "aand").
    # Normalize word boundaries while retaining every expected prose word in order.
    cursor=0;stream=''.join(actual)
    for token in expected:
        found=stream.find(token,cursor)
        if found<0:return False,token
        cursor=found+len(token)
    return True,None

sets=json.loads((BASE/'homework_sets.json').read_text(encoding='utf-8'))
assert len(sys.argv)>1,'Specify homework directories; unscoped PDF verification is disabled'
assert set(sys.argv[1:]) <= {hw['directory'] for hw in sets},'Unknown homework directory'
all_results=[]
for hw in sets:
    if len(sys.argv)>1 and hw['directory'] not in sys.argv[1:]:continue
    root=BASE/hw['directory'];q=read_sections(root/'题目.md');answers=solution_sections(root,q)
    local_meta=root/'homework.json'
    if local_meta.exists():
        local=json.loads(local_meta.read_text(encoding='utf-8'))
        assert local['directory']==hw['directory'] and local['ids']==hw['ids']
        hw.update(local)
    assert list(q)==hw['ids']==list(answers)
    out=root/'review/pdf-pages';out.mkdir(parents=True,exist_ok=True)
    results=[]
    for module,key in [('questions','question_pdf'),('writing','writing_pdf'),('solutions','solution_pdf')]:
        # Retain already verified physics question/writing PDFs without rebuilding.
        built=root/'build'/(module+'.pdf')
        src=built if built.exists() else root/hw[key]
        pdf=PdfReader(src);texts=[p.extract_text() for p in pdf.pages]
        n=len(pdf.pages)
        ids=re.findall(PROBLEM_HEADING,'\n'.join(texts),re.M)
        assert ids==hw['ids'],(src,ids)
        if module=='writing':
            assert n==2*len(q)
            for i,t in enumerate(texts):
                assert all(x not in t for x in ['学号','姓名','学生名字'])
                if i%2:assert not t.strip(),(src,i,'Continuation has text')
                else:
                    assert re.findall(PROBLEM_HEADING,t,re.M)==[hw['ids'][i//2]]
                    assert all(''.join(x.split()) in ''.join(t.split()) for x in ['作图区','过程书写区',hw['label']])
                assert not pdf.pages[i].images
        else:
            if module=='questions':
                if hw.get('questions_per_page',2)==2:assert n==math.ceil(len(q)/2)
                else:assert n>=len(q)
            else:assert sum(t.count('Solution') for t in texts)==len(q)
            # Every prose word in the frozen stem must remain in order in the PDF.
            parts=re.split(PROBLEM_HEADING,'\n'.join(texts),flags=re.M)
            for i in range(1,len(parts),2):
                id=parts[i];body=parts[i+1].split('Solution')[0] if module=='solutions' else parts[i+1]
                expected_stem=answers[id].split('#### Solution')[0].strip() if module=='solutions' else q[id]
                ok,missing=covers(stem_words(expected_stem),words(body))
                assert ok,(src,id,'Missing/out-of-order source word',missing)
            images=sum(len(p.images) for p in pdf.pages)
            expected_images=sum(s.count('![') for s in (answers if module=='solutions' else q).values())
            assert images==expected_images,(src,images,expected_images)
        log=src.with_suffix('.log')
        if log.exists():
            content=log.read_text(encoding='utf-8',errors='replace')
            assert not re.search(r'Overfull \\[hv]box|Missing character:',content),src
        doc=pdfium.PdfDocument(src);tiles=[]
        for i in range(n):
            p=doc[i]
            assert abs(p.get_width()-595.28)<1 and abs(p.get_height()-841.89)<1
            im=p.render(scale=1.5).to_pil().convert('RGB');im.save(out/f'{module}-{i+1:02}.png')
            tile=Image.new('RGB',(340,495),'#e8edf0');thumb=ImageOps.contain(im,(324,462));tile.paste(thumb,(8,4))
            ImageDraw.Draw(tile).text((10,475),f'{hw["label"]} / {module} / {i+1}',fill='black');tiles.append(tile)
            p.close()
        doc.close()
        cols=4 if module=='writing' else 3
        for start in range(0,n,12):
            subset=tiles[start:start+12];sheet=Image.new('RGB',(cols*340,495*math.ceil(len(subset)/cols)),'white')
            for i,tile in enumerate(subset):sheet.paste(tile,((i%cols)*340,(i//cols)*495))
            sheet.save(out/f'{module}-contact-{start//12+1}.png')
        results.append({'module':module,'file':hw[key],'verified_source':str(src),'pages':n,'sha256':sha(src),
                        'ids':ids,'checks':'PASS','source_prose_order':'PASS' if module!='writing' else 'not applicable',
                        'a4':True,'images':sum(len(p.images) for p in pdf.pages)})
        print(hw['label']+' '+module+' '+str(n)+' pages verified',flush=True)
    report={'homework':hw['label'],'subject':hw['subject'],'ids':hw['ids'],'markdown':{f:sha(root/f) for f in ['题目.md','答案册-英文.md','答案册.md']},
            'solution_language':'Chinese prose with original English technical terms','translation_review':json.loads((root/'review/translation/accepted.json').read_text(encoding='utf-8')),
            'pdf_structure':'PASS','visual_review':'PENDING','pdfs':results}
    (root/'review/final-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    all_results.append(report)
if len(sys.argv)>1 and '--local-only' not in sys.argv:
    index=BASE/'workflow/verification.json'
    previous=json.loads(index.read_text(encoding='utf-8')) if index.exists() else []
    replaced={(x['subject'],x['homework']) for x in all_results}
    all_results=[x for x in previous if (x['subject'],x['homework']) not in replaced]+all_results
if '--local-only' not in sys.argv:
    (BASE/'workflow/verification.json').write_text(json.dumps(all_results,ensure_ascii=False,indent=2),encoding='utf-8')
