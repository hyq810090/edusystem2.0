"""Back up and upgrade metadata plus all existing bank difficulty fields."""
import json
import sqlite3
import time
from engine.mathbank.difficulty import fix_metadata, normalize


def migrate(store):
    books = {row['id']: json.loads(row['metadata']) for row in store.rows('SELECT id,metadata FROM textbooks')}
    updated = {bid: fix_metadata(metadata) for bid, metadata in books.items()}
    book_changes = [(bid, value) for bid, value in updated.items() if value != books[bid]]
    bank_changes = []
    for space in store.rows('SELECT id,textbook_id FROM spaces'):
        path = store.DATA/'banks'/str(space['id'])/'math_question_bank.db'
        if not path.exists():
            continue
        labels = updated[space['textbook_id']].get('legacy_difficulty_labels', {})
        c = sqlite3.connect(path)
        try:
            rows = c.execute('SELECT id,difficulty,tags FROM questions').fetchall()
        finally:
            c.close()
        changes = []
        for qid, difficulty, tags in rows:
            new_difficulty, new_tags = normalize(difficulty, tags, labels)
            if (new_difficulty, new_tags) != (difficulty, tags):
                changes.append((new_difficulty, new_tags, qid))
        if changes:
            bank_changes.append((path, changes))
    if not book_changes and not bank_changes:
        return
    # Finish every online backup before touching any question or textbook.
    directory = store.DATA/'migration_backups'/('difficulty-1-5-'+str(time.time_ns()))
    directory.mkdir(parents=True)
    for path in [store.DATA/'platform.db', *[p for p, _ in bank_changes]]:
        name = 'platform.db' if path.name=='platform.db' else f'bank-{path.parent.name}.db'
        target = directory/name
        source, backup = sqlite3.connect(path), sqlite3.connect(target)
        try:
            source.backup(backup)
        finally:
            source.close()
            backup.close()
        target.chmod(0o600)
    for path, changes in bank_changes:
        c = sqlite3.connect(path)
        try:
            with c:
                c.executemany('UPDATE questions SET difficulty=?,tags=? WHERE id=?', changes)
        finally:
            c.close()
    with store.db() as c:
        for bid, metadata in book_changes:
            c.execute('UPDATE textbooks SET metadata=? WHERE id=?', (json.dumps(metadata,ensure_ascii=False),bid))
    (directory/'manifest.json').write_text(json.dumps({
        'rule':'Legacy difficulty retained as tags; no inferred 1-5 rating.',
        'books':len(book_changes), 'banks':{p.parent.name:len(changes) for p,changes in bank_changes},
    },ensure_ascii=False,indent=2),encoding='utf-8')
