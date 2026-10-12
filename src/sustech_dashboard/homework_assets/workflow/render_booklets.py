"""The common, scoped Markdown-to-TeX step for the three homework booklets."""
from pathlib import Path
import re,json,sys,hashlib

BASE=Path(__file__).resolve().parent.parent
PREAMBLE=r'''% !TEX program = tectonic
\documentclass[11pt,a4paper,UTF8,fontset=none]{ctexart}
\usepackage[margin=18mm]{geometry}
\usepackage{amsmath,amssymb,graphicx,xcolor,tikz,fancyhdr}
\setmainfont{Times New Roman}
\setCJKmainfont{SimSun}
\setCJKsansfont{Microsoft YaHei}
\definecolor{Ink}{HTML}{203D50}
\definecolor{Muted}{HTML}{60717A}
\definecolor{Rule}{HTML}{C4CDD2}
\setlength{\parindent}{0pt}
\setlength{\parskip}{0.4em}
\setlength{\emergencystretch}{2em}
\setlength{\headheight}{14pt}
\pagestyle{fancy}\fancyhf{}
\renewcommand{\headrulewidth}{0pt}
\fancyfoot[R]{\small\color{Muted}\thepage}
\newcommand{\circled}[1]{\tikz[baseline=(p.base)]\node[draw,circle,inner sep=1pt] (p) {$\displaystyle #1$};}
\newcommand{\displaymathfit}[1]{\par\vspace{2pt}\begingroup
\setbox0=\hbox{$\displaystyle #1$}
\noindent\makebox[\linewidth][c]{\ifdim\wd0>\linewidth
\typeout{DISPLAY-FIT: \the\wd0\space into \the\linewidth}
\resizebox{\linewidth}{!}{\usebox0}\else\usebox0\fi}
\endgroup\par\vspace{2pt}}
\begin{document}
'''

def esc(t):
    mapping={'&':r'\&','%':r'\%','#':r'\#','_':r'\_','{':r'\{','}':r'\}',
             '’':"'",'—':'--','–':'--','×':r'$\times$','∞':r'$\infty$'}
    return ''.join(mapping.get(c,c) for c in t)

def read_sections(p):
    text=p.read_text(encoding='utf-8')
    parts=re.split(r'^### Problem ([\d.]+)\s*$',text,flags=re.M)
    return {parts[i]:parts[i+1].strip() for i in range(1,len(parts),2)}

def solution_sections(root,questions):
    english=root/'答案册-英文.md';target=root/'答案册.md'
    originals=read_sections(english)
    sections=read_sections(target)
    assert list(originals)==list(questions)==list(sections)
    hw=json.loads((root/'homework.json').read_text(encoding='utf-8')) if (root/'homework.json').exists() else {}
    source_questions=read_sections(root/'题目-英文.md') if hw.get('answer_stems_translated') else questions
    for id in originals:
        assert originals[id].split('#### Solution',1)[0].strip()==source_questions[id]
        if hw.get('answer_stems_translated'):
            assert sections[id].split('#### Solution',1)[0].strip()==questions[id]
    receipt=json.loads((root/'review/translation/accepted.json').read_text(encoding='utf-8'))
    assert receipt['status']=='PASS'
    assert hashlib.sha256(english.read_bytes()).hexdigest()==receipt['source_sha256']
    assert hashlib.sha256(target.read_bytes()).hexdigest()==receipt['translation_sha256']
    return sections

def convert(body,root):
    pattern=r'(!\[[^\]]*\]\([^)]*\)|\$\$[\s\S]*?\$\$|\$[^$]+\$|\*\*|^#### [^\n]+$|^---\s*$)'
    parts=re.split(pattern,body,flags=re.M)
    out=[];bold=False
    for part in parts:
        if part=='**':
            out.append('}' if bold else r'\textbf{');bold=not bold
        elif part.startswith('!['):
            name=re.search(r'\]\((.*?)\)',part).group(1)
            path=(root/name).resolve();assert path.is_file()
            out.append(r'\begin{center}\includegraphics[width=0.8\linewidth,height=5.2cm,keepaspectratio]{'+path.as_posix()+r'}\end{center}')
        elif part.startswith('$$'):
            math=part[2:-2].strip()
            math=re.sub(r'\\tag\{(.*?)\}',lambda m:r'\qquad\text{('+m.group(1)+')}',math)
            out.append('\n\\displaymathfit{'+math+'}\n')
        elif part.startswith('$'):out.append(part)
        elif part.startswith('#### '):
            out.append('\n'+r'{\large\bfseries\color{Ink}'+esc(part[5:])+r'}\par'+'\n')
        elif part.strip()=='---':out.append('\n'+r'{\color{Rule}\hrule}'+'\n')
        else:
            part=esc(part)
            part=re.sub(r'^- ',lambda m:r'\par\noindent\textbullet\ ',part,flags=re.M)
            out.append(part)
    assert not bold,'Unclosed emphasis'
    return ''.join(out)

def writing_page(hw,id,index):
    return r'''\begin{tikzpicture}[remember picture,overlay]
\begin{scope}[shift={(current page.north west)},x=1mm,y=-1mm]
\node[anchor=north west,text=Ink,font=\Large\bfseries,inner sep=0] at (18,17) {'''+esc(hw['subject'])+r'''作业};
\node[anchor=north east,text=Muted,font=\small,inner sep=0] at (192,19) {'''+esc(hw['label'])+r'''};
\node[anchor=north west,text=Ink,font=\normalsize\bfseries,inner sep=0] at (18,31) {Problem '''+id+r'''};
\draw[Rule,line width=0.6pt] (18,41) -- (192,41);
\node[anchor=north west,text=Muted,font=\small,inner sep=0] at (18,46) {作图区};
\draw[Rule,line width=0.55pt] (18,54) rectangle (192,141);
\node[anchor=north west,text=Muted,font=\small,inner sep=0] at (18,148) {过程书写区};
\foreach \y in {157,165,...,277} {\draw[Rule,line width=0.32pt] (18,\y) -- (192,\y);}
\node[anchor=south east,text=Muted,font=\scriptsize,inner sep=0] at (192,289) {'''+str(index+1)+' / '+str(len(hw['ids']))+r'''};
\end{scope}\end{tikzpicture}\null
\clearpage
\begin{tikzpicture}[remember picture,overlay]
\begin{scope}[shift={(current page.north west)},x=1mm,y=-1mm]
\foreach \y in {21,29,...,277} {\draw[Rule,line width=0.32pt] (18,\y) -- (192,\y);}
\end{scope}\end{tikzpicture}\null
'''

def build(hw,modules):
    root=BASE/hw['directory'];out=root/'build';out.mkdir(exist_ok=True)
    questions=read_sections(root/'题目.md');assert list(questions)==hw['ids']
    questions_per_page=int(hw.get('questions_per_page',2));assert questions_per_page in [1,2]
    for module in modules:
        sections=solution_sections(root,questions) if module=='solutions' else questions
        assert list(sections)==hw['ids']
        preamble=PREAMBLE
        if module=='writing':preamble=preamble.replace(r'\pagestyle{fancy}',r'\pagestyle{empty}')
        if module=='solutions':
            preamble=preamble.replace('[11pt,a4paper,','[10pt,a4paper,')
            solution_spacing=float(hw.get('solution_parskip_em',0.15))
            assert 0<=solution_spacing<=0.4
            preamble=preamble.replace(r'\setlength{\parskip}{0.4em}',r'\setlength{\parskip}{'+str(solution_spacing)+'em}')
            preamble=preamble.replace(r'\vspace{2pt}',r'\vspace{0pt}')
            if 'solution_line_spread' in hw:
                spread=float(hw['solution_line_spread']);assert 1.0<=spread<=1.3
                preamble=preamble.replace(r'\begin{document}',r'\linespread{'+str(spread)+r'}\begin{document}')
        parts=[preamble]
        for i,id in enumerate(hw['ids']):
            if module=='writing':
                if i:parts.append('\n\\clearpage\n')
                parts.append(writing_page(hw,id,i));continue
            newpage=(module=='solutions' or i%questions_per_page==0)
            if newpage:
                if i:parts.append('\n\\clearpage\n')
                title=hw['subject']+' · '+hw['label']+' · '+('答案册' if module=='solutions' else '题目册')
                parts.append(r'{\Large\color{Ink}'+esc(title)+r'}\par\vspace{3mm}'+'\n')
            if module=='questions' and questions_per_page==2:parts.append(r'\begin{minipage}[t][0.445\textheight][t]{\textwidth}'+'\n')
            parts.append(r'{\large\bfseries\color{Ink}Problem '+id+r'}\par\vspace{1mm}'+'\n')
            parts.append(convert(sections[id],root)+'\n')
            if module=='questions' and questions_per_page==2:
                parts.append(r'\end{minipage}'+'\n')
                if i%2==0:parts.append(r'\par\vspace{3mm}{\color{Rule}\hrule}\vspace{4mm}'+'\n')
        parts.append('\n\\end{document}\n')
        (out/(module+'.tex')).write_text(''.join(parts),encoding='utf-8')
        print(hw['label']+' '+module+' TeX generated',flush=True)
    snapshot={name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in ['题目.md','答案册-英文.md','答案册.md'] if(root/name).exists()}
    (out/'source-hashes.json').write_text(json.dumps(snapshot,indent=2),encoding='utf-8')

if __name__=='__main__':
    sets=json.loads((BASE/'homework_sets.json').read_text(encoding='utf-8'))
    selected=sys.argv[1];modules=sys.argv[2:]
    for hw in sets:
        if hw['directory']==selected:build(hw,modules)
