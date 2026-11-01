"""Space-authorized adapter for upstream's bounded SQLite read-only reader.

The main database is always opened mode=ro. Catalogue projection uses only
in-memory TEMP tables on this connection, never schema changes in the bank.
"""
import json

from platform_app import knowledge_points


def register(engine, config):
    from mathbank.agent_api import create_agent_router
    from mathbank.agent_reads import AgentReader
    from mathbank import paths

    points_enabled = config.get('subject') == '数学' and config.get('stage') == '高中'

    class PlatformReader(AgentReader):
        def _version(self, version_code):
            selected = super()._version(version_code)
            if selected != self.active_version_code:
                raise ValueError('空间教材固定，请在对应教材的空间读取')
            return selected

        def __enter__(self):
            super().__enter__()
            try:
                if points_enabled and 'platform_question_points' in self.tables:
                    connection = self.connection
                    # mode=ro still prohibits writes to main even while building TEMP projections.
                    connection.execute('PRAGMA query_only=OFF')
                    connection.execute('''CREATE TEMP TABLE knowledge_points(
                        code TEXT PRIMARY KEY,name TEXT,original_name TEXT,source TEXT,aliases TEXT)''')
                    connection.execute('''CREATE TEMP TABLE knowledge_point_curriculums(
                        id INTEGER PRIMARY KEY,point_code TEXT,version_code TEXT,compulsory TEXT,
                        chapter TEXT,knowledge TEXT,reference_section TEXT,level TEXT)''')
                    catalogue = knowledge_points.catalogue()['points']
                    connection.executemany('INSERT INTO temp.knowledge_points VALUES (?,?,?,?,?)',
                        [(p['code'], p['name'], p['name'], knowledge_points.catalogue()['source'],
                          json.dumps(p['aliases'], ensure_ascii=False)) for p in catalogue])
                    curriculum = engine.get_current_curriculum()
                    for point in knowledge_points.edition_points(engine.get_active_version_code(), curriculum):
                        for path in point.get('paths', []):
                            connection.execute('''INSERT INTO temp.knowledge_point_curriculums
                                (point_code,version_code,compulsory,chapter,knowledge,reference_section,level)
                                VALUES (?,?,?,?,?,?,?)''', (point['code'], engine.get_active_version_code(),
                                *path, '', ''))
                    connection.execute('''CREATE TEMP VIEW question_knowledge_points AS
                        SELECT rowid AS id,question_id,code AS point_code,role,position,
                        'confirmed' AS status,'' AS scope,evidence_source,evidence,reason,origin
                        FROM main.platform_question_points''')
                    self.tables.update({'knowledge_points', 'knowledge_point_curriculums', 'question_knowledge_points'})
                    connection.execute('PRAGMA query_only=ON')
                return self
            except Exception:
                self.close()
                raise

        def capabilities(self):
            value = super().capabilities()
            value['content_sources_note'] = '逐题 source 保留出处；平台来源记录随跨空间复制保存。'
            value['subject'] = config.get('subject')
            value['stage'] = config.get('stage')
            value['curriculum_versions'] = [{'value': self.active_version_code, 'label': '当前空间教材'}]
            value['difficulties'] = [item for item in value['difficulties'] if item['value'] in {'1', '2', '3', '4', '5'}]
            value['unrated_count'] = self._execute(
                "SELECT COUNT(*) FROM questions WHERE difficulty IS NULL OR difficulty NOT IN ('1','2','3','4','5')"
            ).fetchone()[0]
            return value

    engine.app.include_router(create_agent_router(
        database_path=lambda: paths.DATABASE_FILE, uploads_dir=lambda: paths.UPLOADS_DIR,
        metadata_path=lambda: paths.DATA_BACKUP_DIR / 'custom_metadata.json',
        active_version_code=engine.get_active_version_code, reader_factory=PlatformReader,
    ))
