"""Platform database: accounts, teams, textbooks and spaces.

A space is exactly one native MathBank bank. Its subject, stage and textbook
come from the textbook chosen at creation and never change; the bank lives in
DATA/banks/<space id>/.
"""
from contextlib import contextmanager
from pathlib import Path
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time

from engine.mathbank.difficulty import fix_metadata

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get('MATHBANK_PLATFORM_DATA', ROOT / 'data')).resolve()

# Roles that work with question banks. Any future role (e.g. students) gets no
# space access unless permission() is extended for it explicitly.
TEACHER_ROLES = {'super_admin', 'team_admin', 'member'}

# Statements are kept separate so migrations can recreate tables inside one
# transaction (sqlite3's executescript would commit midway).
SCHEMA = {
    'teams': 'CREATE TABLE teams(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE)',
    'users': '''CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL,
      password TEXT NOT NULL, role TEXT NOT NULL, team_id INTEGER REFERENCES teams(id),
      enabled INTEGER NOT NULL DEFAULT 1, last_space_id INTEGER)''',
    'sessions': '''CREATE TABLE sessions(token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
      csrf TEXT NOT NULL, expires REAL NOT NULL)''',
    'textbooks': '''CREATE TABLE textbooks(id INTEGER PRIMARY KEY, stage TEXT NOT NULL, subject TEXT NOT NULL,
      name TEXT NOT NULL, engine_version TEXT NOT NULL DEFAULT 'A', metadata TEXT NOT NULL,
      enabled INTEGER NOT NULL DEFAULT 1)''',
    'spaces': '''CREATE TABLE spaces(id INTEGER PRIMARY KEY, name TEXT NOT NULL,
      kind TEXT NOT NULL CHECK(kind IN ('personal','team','public')),
      owner_id INTEGER REFERENCES users(id), team_id INTEGER REFERENCES teams(id),
      textbook_id INTEGER NOT NULL REFERENCES textbooks(id), created REAL NOT NULL)''',
    'space_textbook_fixed': '''CREATE TRIGGER space_textbook_fixed BEFORE UPDATE OF textbook_id ON spaces
      WHEN NEW.textbook_id<>OLD.textbook_id BEGIN SELECT RAISE(ABORT,'space textbook is fixed'); END''',
    'members': '''CREATE TABLE members(space_id INTEGER NOT NULL REFERENCES spaces(id),
      user_id INTEGER NOT NULL REFERENCES users(id),
      permission TEXT NOT NULL CHECK(permission IN ('viewer','editor')), PRIMARY KEY(space_id,user_id))''',
    'folders': '''CREATE TABLE folders(id INTEGER PRIMARY KEY, space_id INTEGER NOT NULL REFERENCES spaces(id),
      kind TEXT NOT NULL, name TEXT NOT NULL, parent_id INTEGER REFERENCES folders(id))''',
    'paper_folders': '''CREATE TABLE paper_folders(space_id INTEGER NOT NULL REFERENCES spaces(id),
      paper_id INTEGER NOT NULL, folder_id INTEGER REFERENCES folders(id), PRIMARY KEY(space_id,paper_id))''',
    # Cross-space reference copies; the source may later change or disappear.
    'origins': '''CREATE TABLE origins(space_id INTEGER NOT NULL REFERENCES spaces(id), question_id INTEGER NOT NULL,
      source_space_id INTEGER NOT NULL, source_question INTEGER NOT NULL, source_space_name TEXT NOT NULL,
      copied_by INTEGER REFERENCES users(id), created REAL NOT NULL, PRIMARY KEY(space_id,question_id))''',
    'tasks': '''CREATE TABLE tasks(id TEXT PRIMARY KEY, space_id INTEGER NOT NULL REFERENCES spaces(id),
      user_id INTEGER NOT NULL REFERENCES users(id))''',
    # 讲义: one JSON document each (see platform_app/handouts.py and docs/HANDOUTS.md).
    'handouts': '''CREATE TABLE handouts(id INTEGER PRIMARY KEY, space_id INTEGER NOT NULL REFERENCES spaces(id),
      folder_id INTEGER REFERENCES folders(id), title TEXT NOT NULL, document TEXT NOT NULL,
      revision INTEGER NOT NULL DEFAULT 1, created_by INTEGER REFERENCES users(id),
      updated_by INTEGER REFERENCES users(id), created REAL NOT NULL, updated REAL NOT NULL)''',
    # 绘图: GeoGebra constructions (.ggb as base64) with a trimmed PNG (see platform_app/drawings.py).
    'drawings': '''CREATE TABLE drawings(id INTEGER PRIMARY KEY, space_id INTEGER NOT NULL REFERENCES spaces(id),
      title TEXT NOT NULL, ggb TEXT NOT NULL DEFAULT '', png BLOB, width INTEGER, height INTEGER,
      chat TEXT NOT NULL DEFAULT '[]', revision INTEGER NOT NULL DEFAULT 1, created_by INTEGER REFERENCES users(id),
      updated_by INTEGER REFERENCES users(id), created REAL NOT NULL, updated REAL NOT NULL)''',
    'settings': 'CREATE TABLE settings(scope TEXT PRIMARY KEY, content TEXT NOT NULL)',
    # Also the change feed for bank writes (action 'bank:<METHOD> <path>').
    'audit': '''CREATE TABLE audit(id INTEGER PRIMARY KEY, user_id INTEGER, action TEXT NOT NULL, target TEXT,
      created REAL NOT NULL)''',
    'platform_migrations': 'CREATE TABLE platform_migrations(name TEXT PRIMARY KEY, created REAL NOT NULL)',
}

SPACE_SELECT = '''SELECT s.*, t.subject, t.stage, t.name AS textbook FROM spaces s
  JOIN textbooks t ON t.id=s.textbook_id'''


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 310000).hex()
    return salt + ':' + digest


def check_password(password, hashed):
    return hmac.compare_digest(password_hash(password, hashed.split(':')[0]), hashed)


def connect(path=None):
    connection = sqlite3.connect(path or DATA / 'platform.db', timeout=30)
    connection.row_factory = sqlite3.Row
    return connection


@contextmanager
def db():
    connection = connect()
    connection.execute('PRAGMA foreign_keys=ON')
    try:
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def rows(sql, args=()):
    with db() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def one(sql, args=()):
    result = rows(sql, args)
    return result[0] if result else None


def backup(label):
    """Online copy of platform.db taken before a structural change."""
    directory = DATA / 'migration_backups'
    directory.mkdir(exist_ok=True)
    target = directory / f'before-{label}-{time.time_ns()}.db'
    source, copy = connect(), sqlite3.connect(target)
    try:
        source.backup(copy)
    finally:
        source.close()
        copy.close()
    target.chmod(0o600)
    return target


def initialize():
    from platform_app import migrations
    DATA.mkdir(parents=True, exist_ok=True)
    with db() as c:
        c.execute('PRAGMA journal_mode=WAL')
        fresh = not c.execute("SELECT 1 FROM sqlite_master WHERE name='users'").fetchone()
        if fresh:
            for statement in SCHEMA.values():
                c.execute(statement)
            c.executemany('INSERT INTO platform_migrations VALUES (?,?)',
                          [(name, time.time()) for name in migrations.NAMES])
    if not fresh:
        migrations.apply()
    seed()


def seed():
    with db() as c:
        if not c.execute('SELECT 1 FROM users').fetchone():
            password = os.environ.get('MATHBANK_ADMIN_PASSWORD') or secrets.token_urlsafe(18)
            c.execute("INSERT INTO teams(name) VALUES ('默认教研团队')")
            c.execute('INSERT INTO users(username,display_name,password,role,team_id) VALUES (?,?,?,?,1)',
                      ('admin', '超级管理员', password_hash(password), 'super_admin'))
            credentials = DATA / 'initial-admin.txt'
            credentials.write_text('用户名：admin\n密码：' + password + '\n请登录后修改初始密码。\n', encoding='utf-8')
            credentials.chmod(0o600)
        if not c.execute('SELECT 1 FROM textbooks').fetchone():
            types = [{'value': k, 'label': v} for k, v in [('single_choice', '单选题'), ('multi_choice', '多选题'),
                                                          ('fill_in_blank', '填空题'), ('detailed_answer', '解答题')]]
            for version, name in {'A': '人教A版', 'B': '人教B版', 'S': '苏教版', 'H': '沪教版'}.items():
                curriculum = json.loads((ROOT / 'engine/mathbank/resources/curriculums' / f'{version}.json').read_text(encoding='utf-8'))
                metadata = fix_metadata({'curriculum': curriculum, 'question_types': types, 'grades': ['高一', '高二', '高三']})
                c.execute('INSERT INTO textbooks(stage,subject,name,engine_version,metadata) VALUES (?,?,?,?,?)',
                          ('高中', '数学', name, version, json.dumps(metadata, ensure_ascii=False)))
        if not c.execute('SELECT 1 FROM spaces').fetchone():
            c.execute("INSERT INTO spaces(name,kind,owner_id,team_id,textbook_id,created) VALUES ('我的备课空间','personal',1,1,1,?)",
                      (time.time(),))


def permission(user, space):
    """Effective grant of a user on a space: owner, editor, viewer or None."""
    if user['role'] not in TEACHER_ROLES:
        return None
    if user['role'] == 'super_admin' or space['owner_id'] == user['id']:
        return 'owner'
    if space['kind'] == 'team' and space['team_id'] == user['team_id']:
        if 'member_permission' in space:
            explicit = space['member_permission']
        else:
            row = one('SELECT permission FROM members WHERE space_id=? AND user_id=?', (space['id'], user['id']))
            explicit = row and row['permission']
        return explicit or ('editor' if user['role'] == 'team_admin' else 'viewer')
    if space['kind'] == 'public':
        return 'viewer'
    return None


def get_space(space_id):
    return one(SPACE_SELECT + ' WHERE s.id=?', (space_id,))


def accessible_spaces(user):
    spaces = rows('''SELECT s.*, t.subject, t.stage, t.name AS textbook, u.display_name AS owner_name,
        m.permission AS member_permission
      FROM spaces s JOIN textbooks t ON t.id=s.textbook_id LEFT JOIN users u ON u.id=s.owner_id
      LEFT JOIN members m ON m.space_id=s.id AND m.user_id=?
      ORDER BY t.subject, t.stage, s.id''', (user['id'],))
    result = []
    for space in spaces:
        space['permission'] = permission(user, space)
        del space['member_permission']
        if space['permission']:
            result.append(space)
    return result


def audit(user, action, target):
    with db() as c:
        c.execute('INSERT INTO audit(user_id,action,target,created) VALUES (?,?,?,?)',
                  (user['id'], action, str(target), time.time()))


def config(scope):
    row = one('SELECT content FROM settings WHERE scope=?', (scope,))
    return json.loads(row['content']) if row else {}


def effective_config(space):
    value = config('platform')
    if space['kind'] != 'public' and space['team_id']:
        value.update({k: v for k, v in config('team:' + str(space['team_id'])).items() if v})
    return value
