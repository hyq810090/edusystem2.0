"""Turn a MinerU content list into Markdown that MathBank's import splits reliably.

MinerU's own full.md loses layout: figures come out as a flat sequence with
unreliable A/B/C/D captions, labels drawn inside figures become text lines, and
numbers such as "1 如图" or "例 1." are not recognised as question starts by
MathBank's formula checks. This module rebuilds the Markdown from the block
list (type, page, 0-1000 bounding box) instead:

- question starts are rewritten as "N．";
- text that sits inside or right under a figure is treated as part of it;
- in choice questions whose options are pictures, the option figures are found
  by geometry (a regular row or grid of similar-sized figures) and written as
  "A．![](…)" lines, and any other figure stays with the stem.
"""
from dataclasses import dataclass, field
from itertools import combinations
import re
from statistics import median

FIGURES = {'image', 'chart'}
SKIPPED = {'page_number', 'header', 'footer', 'page_footnote', 'aside_text'}
START = re.compile(r'^\s*(?:例\s*(?P<example>\d{1,3})\s*[.．、:：]?|(?P<number>\d{1,3})\s*(?P<mark>[.．、])'
                   r'|(?P<bare>\d{1,3})\s+(?=[^\s\d.．、]))\s*')
HEADING_NUMBER = re.compile(r'^\s*\d{1,3}\s*[.．、]\s*')
TEXT_OPTIONS = re.compile(r'(?:^|\s)[A-D]\s*[.．、]\s*\S')
CHOICE_BLANK = re.compile(r'[（(]\s*[）)]')
OPTION_LABEL = re.compile(r'^\s*[（(]?[A-D][）)]?\s*[.．、]?\s*$')
LETTERS = 'ABCD'


@dataclass
class Question:
    number: int
    blocks: list = field(default_factory=list)


def _center(box):
    return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2


def _inside_figure(block, figures):
    """Labels drawn in a figure (or printed right under it) belong to the figure."""
    text = re.sub(r'\s', '', block.get('text', ''))
    x, y = _center(block['bbox'])
    for figure in figures:
        if figure['page_idx'] != block['page_idx']:
            continue
        left, top, right, bottom = figure['bbox']
        if left <= x <= right and top <= y <= bottom and len(text) <= 40:
            return True
        if left - 10 <= x <= right + 10 and bottom < y <= bottom + 45 and len(text) <= 12:
            return True
    return False


def _rows(figures):
    """Group figures whose vertical extents overlap into rows, top to bottom."""
    rows = []
    for figure in sorted(figures, key=lambda f: (f['page_idx'], f['bbox'][1])):
        top, bottom = figure['bbox'][1], figure['bbox'][3]
        for row in rows:
            other = row[0]
            overlap = min(bottom, other['bbox'][3]) - max(top, other['bbox'][1])
            if other['page_idx'] == figure['page_idx'] and overlap > 0.5 * min(bottom - top, other['bbox'][3] - other['bbox'][1]):
                row.append(figure)
                break
        else:
            rows.append([figure])
    return [sorted(row, key=lambda f: f['bbox'][0]) for row in rows]


def _grid_order(figures):
    """Reading order if the figures form one row or a column-aligned grid, else None."""
    rows = _rows(figures)
    if len(rows) == 1:
        return rows[0]
    if len({len(row) for row in rows}) != 1:
        return None
    for column in zip(*rows):
        centers = [_center(f['bbox'])[0] for f in column]
        if max(centers) - min(centers) > 60:
            return None
    return [figure for row in rows for figure in row]


def _size_cost(figures):
    widths = [f['bbox'][2] - f['bbox'][0] for f in figures]
    heights = [f['bbox'][3] - f['bbox'][1] for f in figures]
    w, h = median(widths), median(heights)
    deviations = [abs(a - w) / w + abs(b - h) / h for a, b in zip(widths, heights)]
    return None if max(deviations) > 0.7 else sum(deviations)


def picture_options(figures):
    """Option figures in A-D order and the remaining stem figures, or None."""
    best = None
    for size in (4, 3):
        for chosen in combinations(figures, size):
            order = _grid_order(chosen)
            cost = _size_cost(chosen) if order else None
            if cost is not None and (best is None or cost < best[0]):
                best = cost, order
        if best:
            break
    if not best:
        return None
    options = best[1]
    return options, [f for f in figures if f not in options]


def _figure(block):
    return f"![]({block['img_path']})"


def _render_question(question, report):
    figures = [b for b in question.blocks if b['type'] in FIGURES]
    labels = [b for b in question.blocks if b['type'] == 'text' and OPTION_LABEL.match(b.get('text', ''))]
    text = [b for b in question.blocks if b['type'] not in FIGURES and b not in labels]
    body = ' '.join(b.get('text', '') for b in text)
    split = None
    if len(figures) >= 3 and CHOICE_BLANK.search(body) and not TEXT_OPTIONS.search(body):
        split = picture_options(figures)
    lines = []
    if split:
        options, stem_figures = split
        lines += [_block(b) for b in text] + [_figure(f) for f in stem_figures]
        lines += [f'{LETTERS[i]}．{_figure(f)}' for i, f in enumerate(options)]
        report['picture_option_questions'] += 1
        report['stem_figures'] += len(stem_figures)
        report['figure_labels_removed'] += len(labels)
    else:
        lines += [_block(b) if b['type'] not in FIGURES else _figure(b) for b in question.blocks]
    return lines


def _block(block):
    kind = block['type']
    if kind == 'table':
        return block.get('table_body') or (f"![]({block['img_path']})" if block.get('img_path') else '')
    if kind == 'list':
        return '\n'.join(block.get('list_items') or [])
    return block.get('text', '')


def convert(content_list):
    """Return (markdown, figure paths in use, report)."""
    blocks = [b for b in content_list if b.get('type') not in SKIPPED and (b.get('text') or b.get('img_path') or b.get('table_body') or b.get('list_items'))]
    figures = [b for b in blocks if b['type'] in FIGURES]
    report = {'questions': 0, 'renumbered': 0, 'picture_option_questions': 0, 'stem_figures': 0, 'figure_labels_removed': 0}
    lines, question, previous = [], None, 0

    def close():
        nonlocal question
        if question:
            lines.extend(_render_question(question, report))
            question = None

    for block in blocks:
        if block.get('text_level'):
            close()
            title = HEADING_NUMBER.sub('', block['text']).strip()
            lines.append('#' * min(block['text_level'] + 1, 6) + ' ' + title)
            continue
        if block['type'] == 'text' and _inside_figure(block, figures):
            report['figure_labels_removed'] += 1
            continue
        match = START.match(block.get('text', '')) if block['type'] == 'text' else None
        number = None
        if match:
            value = match.group('example') or match.group('number') or match.group('bare')
            # A bare "12 …" opens a question only when it continues the numbering.
            if not match.group('bare') or int(value) in (previous + 1, 1):
                number = int(value)
        if number is not None:
            close()
            previous = number
            report['questions'] += 1
            if not match.group('mark'):
                report['renumbered'] += 1
            block = {**block, 'text': f'{number}．' + block['text'][match.end():]}
            question = Question(number, [block])
        elif question:
            question.blocks.append(block)
        else:
            lines.append(_figure(block) if block['type'] in FIGURES else _block(block))
    close()
    markdown = '\n\n'.join(line for line in lines if line.strip()) + '\n'
    used = re.findall(r'!\[\]\(([^)]+)\)', markdown)
    return markdown, list(dict.fromkeys(used)), report
