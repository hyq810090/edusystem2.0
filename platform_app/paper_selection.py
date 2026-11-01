"""Feed upstream selection planning from the existing per-space point table."""
from types import SimpleNamespace

from sqlalchemy import bindparam, column, select, table, text

from platform_app import knowledge_points


def register(engine, config):
    from mathbank import paper_selection
    from mathbank.database import Question

    enabled = (config.get('subject') == '数学' and config.get('stage') == '高中'
               and config.get('version') in knowledge_points.EDITIONS)
    catalogue = knowledge_points.catalogue()['by_code'] if enabled else {}

    def annotations(db, ids):
        if not enabled or not ids:
            return []
        statement = text('SELECT * FROM platform_question_points WHERE question_id IN :ids ORDER BY position')
        statement = statement.bindparams(bindparam('ids', expanding=True))
        return db.execute(statement, {'ids': list(ids)}).mappings().all()

    def attach(db, questions):
        found = {}
        for row in annotations(db, [q.id for q in questions]):
            point = catalogue.get(row['code'])
            if point:
                found.setdefault(row['question_id'], []).append(SimpleNamespace(
                    point_code=point['code'], point=SimpleNamespace(name=point['name']),
                    role=row['role'], status='confirmed', origin=row['origin'],
                    evidence_source=row['evidence_source'], evidence=row['evidence'],
                    reason=row['reason'], scope='',
                ))
        for question in questions:
            question.knowledge_points = found.get(question.id, [])
        return questions

    def main_points(db, ids):
        found = {}
        for row in annotations(db, ids):
            if row['role'] == 'main' and row['code'] in catalogue:
                found.setdefault(row['question_id'], []).append((row['code'], catalogue[row['code']]['name']))
        return found

    def topic_filter(terms):
        codes = [p['code'] for p in catalogue.values()
                 if any(term in ' '.join([p['name'], *p['aliases']]) for term in terms)]
        if not codes:
            return None
        points = table('platform_question_points', column('question_id'), column('code'), column('role'))
        return Question.id.in_(select(points.c.question_id).where(points.c.code.in_(codes), points.c.role == 'main'))

    engine.paper_selection_annotations = attach
    paper_selection.point_rows = main_points
    paper_selection.point_topic_filter = topic_filter
