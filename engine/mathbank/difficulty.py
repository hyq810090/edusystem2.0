"""Platform-wide ordinal difficulty, separate from teaching-purpose tags."""
from copy import deepcopy
import os
import re

LEVELS = ('1', '2', '3', '4', '5')
UNRATED = 'unrated'
NAMES = ('基础', '简单', '中等', '较难', '困难')
COLORS = ('emerald', 'sky', 'amber', 'orange', 'rose')
LEGACY_LABELS = {
    'easy_error': '易错', 'qiangji': '强基', 'competition': '竞赛',
    'contest': '竞赛', 'challenge': '挑战', 'normal': '原难度：常规题',
    'easy': '原难度：简单', 'medium': '原难度：中等', 'hard': '原难度：困难',
}
_legacy_labels = {}


def enabled():
    return os.environ.get('MATHBANK_UNIFIED_DIFFICULTY') == '1'


def configure_labels(labels):
    global _legacy_labels
    _legacy_labels = dict(labels or {})


def metadata():
    return [
        {'value': UNRATED, 'label': '待确认（不计难度）', 'color': 'text-slate-600 bg-slate-50 border-slate-200'},
        *[{'value': value, 'label': f'{value}级 · {name}',
           'color': f'text-{color}-600 bg-{color}-50 border-{color}-200'}
          for value, name, color in zip(LEVELS, NAMES, COLORS)],
    ]


def fix_metadata(value):
    result = deepcopy(value)
    previous_labels = result.get('legacy_difficulty_labels')
    labels = dict(previous_labels) if isinstance(previous_labels, dict) else {}
    previous = result.get('difficulties')
    for item in previous if isinstance(previous, list) else []:
        if not isinstance(item, dict):
            continue
        code = str(item.get('value', ''))
        if code and code not in (*LEVELS, UNRATED):
            labels.setdefault(code, str(item.get('label') or code))
    result['legacy_difficulty_labels'] = labels
    result['difficulties'] = metadata()
    result['difficulty_scale'] = '1-5-v1'
    return result


def normalize(value, tags='', labels=None):
    value = str(value or '').strip()
    if value in LEVELS or value == UNRATED:
        return value, tags or ''
    existing = [v.strip() for v in re.split(r'[,，]', str(tags or '')) if v.strip()]
    if value:
        label = LEGACY_LABELS.get(value)
        if not label:
            previous = str((labels if labels is not None else _legacy_labels).get(value, value))
            label = next((name for name in ('易错', '强基', '竞赛', '挑战')
                          if previous in (name, name+'题')), '原难度：'+previous)
        if label not in existing:
            existing.append(label)
    return UNRATED, ','.join(existing)


def tag_counts(records):
    counts = {'易错': 0, '挑战': 0, '强基': 0, '竞赛': 0}
    for tags in records:
        values = {v.strip() for v in re.split(r'[,，]', str(tags or ''))}
        for name in counts:
            if name in values or name+'题' in values:
                counts[name] += 1
    return counts
