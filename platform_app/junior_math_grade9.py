"""Add the 九年级 volumes to 人教版 junior-high mathematics catalogs.

Catalogs stay fixed once spaces use them, so this only appends volumes to a
book whose catalog stops at 八年级下册: existing volumes, chapters and question
categories are untouched, and the new volumes come last in catalog order.

The volumes are copied from beike's drafted 初中数学 · 人教版 outline
(src/lib/outlines/presets.ts, checked 2026-10). The new-edition 九年级下册 was
not yet published, so that volume keeps the old edition's content without
chapter numbers and is marked 暂定. Both volumes still need a person's review.
"""
from copy import deepcopy
import json

GRADE = '九年级'
LAST_VOLUME = '八年级下册'
VOLUMES = {
    '九年级上册': {
        '第二十五章 一元二次方程': ['25.1 一元二次方程的概念', '25.2 降次——解一元二次方程', '25.3 实际问题与一元二次方程'],
        '第二十六章 二次函数': ['26.1 二次函数的概念', '26.2 二次函数的图象和性质', '26.3 二次函数与一元二次方程', '26.4 实际问题与二次函数'],
        '第二十七章 反比例函数': ['27.1 反比例函数的概念', '27.2 反比例函数的图象和性质', '27.3 实际问题与反比例函数'],
        '第二十八章 旋转': ['28.1 图形的旋转', '28.2 中心对称'],
        '第二十九章 圆': ['29.1 圆的有关概念', '29.2 圆的有关性质', '29.3 弧长和扇形面积'],
        '第三十章 直线与圆的位置关系': ['30.1 直线与圆', '30.2 三角形的内切圆', '30.3 正多边形与圆'],
    },
    '九年级下册（暂定）': {
        '相似': ['图形的相似', '相似三角形', '位似'],
        '锐角三角函数': ['锐角三角函数', '解直角三角形及其应用'],
        '投影与视图': ['投影', '三视图'],
        '概率初步': ['随机事件与概率', '用列举法求概率', '用频率估计概率'],
    },
}


def upgrade(book):
    """Metadata with the 九年级 volumes appended, or None when the book is not a target."""
    metadata = json.loads(book['metadata'])
    curriculum = metadata.get('curriculum')
    if (book['stage'], book['subject']) != ('初中', '数学') or '人教' not in book['name'] \
            or not isinstance(curriculum, dict) or list(curriculum)[-1:] != [LAST_VOLUME] \
            or any(str(volume).startswith(GRADE) for volume in curriculum):
        return None
    result = deepcopy(metadata)
    result['curriculum'].update(deepcopy(VOLUMES))
    if isinstance(result.get('grades'), list) and GRADE not in result['grades']:
        result['grades'].append(GRADE)
    return result


def migrate(store):
    changes = []
    for book in store.rows('SELECT id,stage,subject,name,metadata FROM textbooks'):
        metadata = upgrade(book)
        if metadata:
            changes.append((book, metadata))
    if not changes:
        return
    backup = store.backup('junior-math-grade9')
    with store.db() as c:
        c.executemany('UPDATE textbooks SET metadata=? WHERE id=?',
                      [(json.dumps(metadata, ensure_ascii=False), book['id']) for book, metadata in changes])
    backup.with_suffix('.json').write_text(json.dumps({
        'rule': 'Appended 九年级 volumes only; existing volumes unchanged. Draft catalog, needs review.',
        'textbooks': [{'id': book['id'], 'name': book['name'], 'added_volumes': list(VOLUMES)} for book, _ in changes],
    }, ensure_ascii=False, indent=2), encoding='utf-8')
