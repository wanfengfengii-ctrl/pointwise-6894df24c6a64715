import sqlite3


SCHEMA_VERSION = 1


class SchemaError(ValueError):
    """SQLite 文件无法作为当前版本的采样归档使用。"""


def _version(connection: sqlite3.Connection) -> int:
    return connection.execute('PRAGMA user_version').fetchone()[0]


def _check_schema(connection: sqlite3.Connection, version: int) -> None:
    if version != SCHEMA_VERSION:
        raise SchemaError(f'不支持归档格式版本 {version}')
    rows = connection.execute('PRAGMA table_info(captures)').fetchall()
    columns = tuple((row[1], row[2], row[3], row[5]) for row in rows)
    expected = (('capture_id', 'INTEGER', 0, 1), ('evidence', 'TEXT', 1, 0))
    if columns != expected:
        raise SchemaError('成果表结构与归档版本不符')


def ensure_schema(connection: sqlite3.Connection) -> None:
    version = _version(connection)
    if version != 0:
        _check_schema(connection, version)
        return

    with connection:
        connection.execute('BEGIN IMMEDIATE')
        # 另一个初始化者可能在取得写锁之前已建好归档。
        version = _version(connection)
        if version == 0:
            existing = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name NOT GLOB 'sqlite_*' LIMIT 1"
            ).fetchone()
            if existing is not None:
                raise SchemaError('已有结构的数据库缺少采样归档版本')
            connection.execute(
                'CREATE TABLE captures (capture_id INTEGER PRIMARY KEY, evidence TEXT NOT NULL)'
            )
            connection.execute(f'PRAGMA user_version = {SCHEMA_VERSION}')
            version = _version(connection)
        _check_schema(connection, version)
