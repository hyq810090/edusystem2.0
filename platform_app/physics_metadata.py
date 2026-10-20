"""Physics question types derived from untouched mathematics defaults."""
from copy import deepcopy
import json

LEGACY_TYPES = [('single_choice','单选题'), ('multi_choice','多选题'),
                ('fill_in_blank','填空题'), ('detailed_answer','解答题')]

def upgrade_defaults(metadata):
    result = deepcopy(metadata)
    types = result.get('question_types', [])
    if [(v.get('value'), v.get('label')) for v in types] != LEGACY_TYPES:
        return result
    types[-1]['label'] = '计算题'
    types.insert(3, {'value':'experiment','label':'实验题'})
    types.append({'value':'short_answer','label':'简答题'})
    return result

def migrate(store):
    """One-time upgrade of physics textbooks still using the four math types."""
    changes = []
    for row in store.rows("SELECT id,metadata FROM textbooks WHERE subject='物理'"):
        before = json.loads(row['metadata'])
        after = upgrade_defaults(before)
        if before != after:
            changes.append((json.dumps(after, ensure_ascii=False), row['id']))
    if changes:
        store.backup('physics-types')
        with store.db() as c:
            c.executemany('UPDATE textbooks SET metadata=? WHERE id=?', changes)
