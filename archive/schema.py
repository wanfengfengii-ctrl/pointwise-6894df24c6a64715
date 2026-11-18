import sqlite3


SCHEMA_VERSION = 2

_CAPTURES_DDL = 'CREATE TABLE captures (capture_id INTEGER PRIMARY KEY, evidence TEXT NOT NULL)'
_DIAGNOSTICS_DDL = (
    'CREATE TABLE diagnostics ('
    'diagnostic_id TEXT PRIMARY KEY NOT NULL, '
    'evidence TEXT NOT NULL, '
    'binding TEXT NOT NULL)'
)


class SchemaError(ValueError):
    """SQLite 文件无法作为当前版本的采样归档使用。"""


def _version(connection: sqlite3.Connection) -> int:
    return connection.execute('PRAGMA user_version').fetchone()[0]


def _table_columns(connection: sqlite3.Connection, table: str) -> tuple:
    rows = connection.execute(f'PRAGMA table_info({table})').fetchall()
    return tuple((row[1], row[2], row[3], row[5]) for row in rows)


def _check_captures(connection: sqlite3.Connection) -> None:
    expected = (('capture_id', 'INTEGER', 0, 1), ('evidence', 'TEXT', 1, 0))
    if _table_columns(connection, 'captures') != expected:
        raise SchemaError('成果表结构与归档版本不符')


def _check_diagnostics(connection: sqlite3.Connection) -> None:
    expected = (
        ('diagnostic_id', 'TEXT', 1, 1),
        ('evidence', 'TEXT', 1, 0),
        ('binding', 'TEXT', 1, 0),
    )
    if _table_columns(connection, 'diagnostics') != expected:
        raise SchemaError('拒绝诊断表结构与归档版本不符')


def _check_schema(connection: sqlite3.Connection, version: int) -> None:
    if version not in (1, SCHEMA_VERSION):
        raise SchemaError(f'不支持归档格式版本 {version}')
    _check_captures(connection)
    if version >= 2:
        _check_diagnostics(connection)


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.execute(_CAPTURES_DDL)
    connection.execute(_DIAGNOSTICS_DDL)
    connection.execute(f'PRAGMA user_version = {SCHEMA_VERSION}')


def _initialize(connection: sqlite3.Connection) -> None:
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
            _create_schema(connection)
            version = _version(connection)
        _check_schema(connection, version)


def _upgrade(connection: sqlite3.Connection) -> None:
    """版本 1 只冻结成功成果；补建诊断表后旧成果原样可读。"""
    _check_captures(connection)
    try:
        with connection:
            connection.execute('BEGIN IMMEDIATE')
            if _version(connection) == 1:
                connection.execute(_DIAGNOSTICS_DDL.replace('CREATE TABLE', 'CREATE TABLE IF NOT EXISTS', 1))
                connection.execute(f'PRAGMA user_version = {SCHEMA_VERSION}')
    except sqlite3.Error:
        # 只读归档无法补表或抬版本时，仍按版本 1 读取既有成果。
        if connection.in_transaction:
            connection.rollback()
    version = _version(connection)
    if version == 1:
        _check_schema(connection, 1)
        return
    _check_schema(connection, version)


def ensure_schema(connection: sqlite3.Connection) -> None:
    version = _version(connection)
    if version == 0:
        _initialize(connection)
        return
    if version == 1:
        _upgrade(connection)
        return
    _check_schema(connection, version)
