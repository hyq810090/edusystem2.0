"""讲义导出, run inside a space's worker so it uses the engine's own question
LaTeX conversion, image lookup, XeLaTeX compiler and Word formula conversion.

PDF: the exam-zh class (choices, \\fillin and \\paren work as in papers) plus
templates/handout/mathbank-handout.sty, the one textbook layout. Word mirrors
the same colours, hierarchy and numbering (see docs/HANDOUTS.md).
"""
import os
import re
from pathlib import Path

TEMPLATE_DIR = Path(__file__).resolve().parent / 'templates' / 'handout'
THEOREMS = {'theorem': '定理', 'property': '性质', 'formula': '公式', 'corollary': '推论'}
NOTES = {'note': ('注', 'mbMuted'), 'method': ('方法', 'mbInk'), 'warning': ('易错', 'mbAccent')}
FIGURE_CM = {'small': (5.5, 4.5), 'medium': (8.5, 6.5), 'large': (12.0, 8.5)}
# A question's trailing figure: modest by default, as in a textbook margin.
FIGURE_LIMITS = {'auto': (4.6, 3.8), 'small': (3.8, 3.2), 'medium': (5.2, 4.2), 'large': (7.0, 5.5)}
IMAGE = re.compile(r'!\[.*?\]\(([^)]+)\)')
# Spans left alone by prose escaping: maths, environments, images and existing commands' groups.
PROTECTED = re.compile(r'\$\$.+?\$\$|\$.+?\$|\\\(.+?\\\)|\\\[.+?\\\]|\\begin\{([A-Za-z*]+)\}.*?\\end\{\1\}'
                       r'|!\[[^\]]*\]\([^)]*\)', re.S)
LIST_ITEM = re.compile(r'^\s*(?:([-*•])|(\d{1,2})[.、])\s+(.*)$')


def escape(text):
    """Teacher prose to TeX: escape specials outside maths/images; ____ becomes a blank."""
    out, position = [], 0
    for match in PROTECTED.finditer(text):
        out.append(_escape_plain(text[position:match.start()]))
        out.append(match.group(0))
        position = match.end()
    out.append(_escape_plain(text[position:]))
    return ''.join(out)


def _escape_plain(text):
    text = re.sub(r'_{3,}', r'\\fillin{}', text)
    text = re.sub(r'(?<!\\)([%#&_])', r'\\\1', text)
    text = re.sub(r'(?<!\\)\^', r'\\^{}', text)
    return re.sub(r'\*\*(.+?)\*\*', r'\\textbf{\1}', text)


def lists(text):
    """Lines starting '- ' or '1. ' become itemize/enumerate."""
    out, mode = [], None
    for line in text.split('\n'):
        match = LIST_ITEM.match(line)
        kind = ('enumerate' if match.group(2) else 'itemize') if match else None
        if kind != mode:
            if mode:
                out.append(rf'\end{{{mode}}}')
            if kind:
                out.append(rf'\begin{{{kind}}}[leftmargin=2em, itemsep=1pt, topsep=3pt]')
            mode = kind
        out.append(r'\item ' + match.group(3) if kind else line)
    if mode:
        out.append(rf'\end{{{mode}}}')
    return '\n'.join(out)


class Latex:
    def __init__(self, engine, payload):
        from mathbank.paper_helper import clean_content_for_latex
        self.clean = clean_content_for_latex
        self.engine = engine
        self.payload = payload
        self.teacher = payload['version'] == 'teacher'
        self.document = payload['document']
        self.sources = []  # pseudo questions whose images must be copied
        self.answers = []

    def prose(self, text):
        text = (text or '').replace('\r\n', '\n').strip()
        if not text:
            return ''
        self.sources.append({'question': {'content': text}})
        return self.clean(lists(escape(text)), '', is_answer=True)

    def inline(self, text):
        return escape((text or '').strip())

    def question(self, question, lead=''):
        """A bank question as in papers: anchored images stay put, the trailing figure follows figure_align.

        `lead` (an example's label) starts the stem, so it stays beside a right-hand figure.
        """
        from mathbank.image_layout import split_figure_anchors, normalize_image_layouts
        from mathbank.paper_helper import _inline_tikz_sources
        self.sources.append({'question': question})
        raw = question.get('content') or ''
        sources = _inline_tikz_sources(raw, question.get('content_tikz_assets') or [], question.get('tikz_code') or '')
        # The engine drops the stem's own empty brackets (\paren prints them), as in papers.
        anchored, detached = split_figure_anchors(raw)
        inline_images = bool(IMAGE.search(anchored))
        body = self.clean(anchored, question.get('question_type', ''), preserve_image_positions=inline_images,
                          tikz_sources=sources if inline_images else None,
                          image_layouts=normalize_image_layouts(question.get('image_layouts') or {}, anchored))
        stem, choices = body, ''
        if r'\begin{choices}' in body:
            stem, rest = body.split(r'\begin{choices}', 1)
            choices = r'\begin{choices}' + rest
        stem = lead + stem.strip()
        figures = self.figures(detached, sources, question.get('figure_size'))
        if not figures:
            return (stem + '\n' + choices).strip() or r'\mbox{}'
        align = question.get('figure_align') or 'right'
        if align in {'right', 'choices_right'} and len(figures) == 1:
            # The stem's first baseline lines up with a list number or example label.
            return (rf'\begin{{minipage}}[t]{{0.64\linewidth}}{stem}\end{{minipage}}\hfill'
                    rf'\begin{{minipage}}[t]{{0.32\linewidth}}\vspace{{0pt}}\centering {figures[0]}\end{{minipage}}\par'
                    + '\n' + choices)
        place = {'bottom_left': 'flushleft', 'bottom_right': 'flushright'}.get(align, 'center')
        return (stem + rf'\par\begin{{{place}}}' + r'\quad '.join(figures) + rf'\end{{{place}}}' + '\n' + choices)

    def figures(self, detached, sources, size):
        from mathbank.paper_helper import _tikz_image_key
        width, height = FIGURE_LIMITS.get(size, FIGURE_LIMITS['auto'])
        rendered, seen = [], set()
        for path in IMAGE.findall(detached or ''):
            key = _tikz_image_key(path)
            if key in seen:
                continue
            seen.add(key)
            code = sources.get(key)
            if code:
                if r'\begin{tikzpicture}' not in code:
                    code = '\\begin{tikzpicture}\n' + code + '\n\\end{tikzpicture}'
                rendered.append(rf'\adjustbox{{max width={width}cm,max height={height}cm,keepaspectratio}}{{{code}' + '\n}')
            else:
                rendered.append(rf'\includegraphics[max width={width}cm,max height={height}cm,keepaspectratio]{{{os.path.basename(path)}}}')
        return rendered

    def answer(self, question, override=''):
        text = (override or '').strip()
        if text:
            return self.prose(text)
        answer = (question.get('answer_markdown') or '').strip()
        return self.clean(answer, '', is_answer=True) if answer else r'{\color{mbMuted}（暂无答案）}'

    def block(self, block, section_number):
        kind = block['kind']
        if kind == 'text':
            return self.prose(block['body'])
        if kind == 'definition':
            return rf"\begin{{mbdefinition}}{{{self.inline(block['title'])}}}" + '\n' + self.prose(block['body']) + '\n\\end{mbdefinition}'
        if kind in THEOREMS:
            return (rf"\begin{{mbtheorem}}{{{THEOREMS[kind]}}}{{{self.inline(block['title'])}}}" + '\n'
                    + self.prose(block['body']) + '\n\\end{mbtheorem}')
        if kind == 'proof':
            return '\\begin{mbproof}\n' + self.prose(block['body']) + '\n\\end{mbproof}'
        if kind in NOTES:
            label, colour = NOTES[kind]
            return rf'\begin{{mbnote}}{{{label}}}{{{colour}}}' + '\n' + self.prose(block['body']) + '\n\\end{mbnote}'
        if kind == 'summary':
            return '\\begin{mbsummary}\n' + self.prose(block['body']) + '\n\\end{mbsummary}'
        if kind == 'figure':
            name = os.path.basename(block['image'])
            if not name:
                return ''
            self.sources.append({'question': {'content': f"![]({block['image']})"}})
            width, height = FIGURE_CM[block['size']]
            image = rf'\includegraphics[max width={width}cm,max height={height}cm,keepaspectratio]{{{name}}}'
            return rf'\mbfigure{{{image}}}{{{self.inline(block["caption"])}}}'
        if kind == 'example':
            question = block['question']
            label = rf"\mbexamplelabel{{{self.inline(block['title'])}}}"
            parts = [r'\begin{mbexample}', self.question(question, label), r'\end{mbexample}']
            if self.teacher or self.document['settings']['student_solutions'] == 'show':
                parts += ['\\begin{mbsolution}', self.answer(question, block.get('solution')), '\\end{mbsolution}']
            else:
                parts.append(rf"\mbanswerspace{{{block['space']}}}")
            return '\n'.join(parts)
        if kind == 'exercises':
            title = self.inline(block['title']) or '练习'
            items = [r'\item ' + self.question(question) for question in block['questions']]
            if self.teacher and block['questions']:
                self.answers.append((f'{section_number}\\quad {title}',
                                     [self.answer(question) for question in block['questions']]))
            return rf'\begin{{mbexercises}}{{{title}}}' + '\n' + '\n'.join(items) + '\n\\end{mbexercises}' if items else ''
        return ''

    def render(self):
        payload, document = self.payload, self.document
        body = []
        meta = f"{payload['stage']}{payload['subject']}" + (f" · {payload['textbook']}" if payload.get('textbook') else '')
        body.append(rf"\mbheading{{{self.inline(document['kicker'])}}}{{{self.inline(payload['title'])}}}"
                    rf"{{{self.inline(document['subtitle'])}}}{{{self.inline(meta + ' · ' + ('教师版' if self.teacher else '学生版'))}}}")
        objectives = [item for item in document['objectives'] if item.strip()]
        if objectives:
            body.append('\\begin{mbobjectives}\n' + '\n'.join(r'\item ' + self.inline(item) for item in objectives)
                        + '\n\\end{mbobjectives}')
        for number, section in enumerate(document['sections'], 1):
            body.append(rf"\section{{{self.inline(section['title']) or '　'}}}")
            body.extend(filter(None, (self.block(block, number) for block in section['blocks'])))
        if self.answers:
            body.append(r'\mbanswersheading')
            for title, answers in self.answers:
                body.append(rf'\mbanswergroup{{{title}}}')
                body.append('\\begin{enumerate}[label={\\mbsans\\arabic*.}, leftmargin=2em, itemsep=4pt]\n'
                            + '\n'.join(r'\item ' + answer for answer in answers) + '\n\\end{enumerate}')
        joined = '\n\n'.join(body)
        return '\n'.join([*self.preamble(joined), r'\begin{document}', r'\pagestyle{mbbody}', r'\raggedbottom',
                          joined, r'\end{document}'])

    def preamble(self, body):
        lines = [
            r'\documentclass[fontset=fandol]{exam-zh}',
            # Same mathematics baseline and compatibility macros as paper_helper.build_latex_document.
            r'\usepackage{amsmath,mathtools,cancel,cases,mhchem,siunitx,extarrows}',
            r'\providecommand{\bm}[1]{\symbf{#1}}',
            r'\providecommand{\wideparen}[1]{\overparen{#1}}',
            r'\providecommand{\widearc}[1]{\overparen{#1}}',
            r'\providecommand{\overarc}[1]{\overparen{#1}}',
            r'\providecommand{\degree}{\ensuremath{{}^\circ}}',
            r'\providecommand{\celsius}{\ensuremath{{}^\circ\mathrm{C}}}',
            r'\providecommand{\perthousand}{\ensuremath{\mathord{\text{\textperthousand}}}}',
            r'\providecommand{\permil}{\perthousand}',
            r'\usepackage{array,booktabs,tabularx,longtable,multirow,makecell,diagbox,colortbl}',
            r'\usepackage{tkz-euclide}',
            r'\usepackage{graphicx}',
            r'\graphicspath{{images/}{./}}',
            r'\usepackage[export]{adjustbox}',
        ]
        if re.search(r'\\sfrac(?![A-Za-z])', body):
            lines.append(r'\usepackage{xfrac}')
        if r'\begin{tikzpicture}' in body:
            lines += [r'\usepackage{pgfplots}', r'\pgfplotsset{compat=1.16}',
                      r'\usetikzlibrary{patterns,calc,positioning,intersections,arrows,shapes.geometric,through,'
                      r'decorations.pathmorphing,arrows.meta,quotes,angles,3d,decorations.pathreplacing,decorations.markings}']
        answers = 'true' if self.teacher else 'false'
        lines += [
            r'\examsetup{page/size=a4paper, paren/show-paren=true, paren/show-answer=' + answers
            + r', fillin/show-answer=' + answers + r', font=times, math-font=xits, fillin/no-answer-type=none}',
            r'\usepackage{mathbank-handout}',
            r'\renewcommand\mbrunningleft{' + self.inline(self.payload['title']) + '}',
            r'\renewcommand\mbrunningright{' + self.inline(f"{self.payload['stage']}{self.payload['subject']} · 讲义") + '}',
        ]
        return lines

    def images(self):
        from mathbank.paper_helper import collect_referenced_images
        return collect_referenced_images(self.sources, self.engine.UPLOAD_DIR, self.engine.UPLOAD_DIR_REL)


def pdf(engine, payload):
    builder = Latex(engine, payload)
    tex = builder.render()
    data, log = engine.compile_tex_to_pdf(tex, builder.images())
    return (data, None) if data else (None, 'PDF 编译失败：' + str(log)[-1500:])


def register(engine):
    """Add the private /internal/handout route to the worker app."""
    from fastapi import Request
    from fastapi.responses import JSONResponse, Response
    from starlette.concurrency import run_in_threadpool

    os.environ['TEXINPUTS'] = str(TEMPLATE_DIR) + '//' + os.pathsep + os.environ.get('TEXINPUTS', '')

    @engine.app.post('/internal/handout')
    async def export(request: Request):
        payload = await request.json()
        build = pdf if payload.get('format') == 'pdf' else docx
        data, error = await run_in_threadpool(build, engine, payload)
        if data is None:
            return JSONResponse({'detail': error}, status_code=422)
        media = 'application/pdf' if payload.get('format') == 'pdf' else \
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        return Response(data, media_type=media)


def docx(engine, payload):
    from platform_app.handout_word import build
    return build(engine, payload)
