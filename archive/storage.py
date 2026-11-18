import sqlite3
from collections.abc import Callable
from pathlib import Path

from admission.evidence import CaptureEvidence
from archive.artifacts import CaptureNotFound, FrozenCapture
from archive.codec import EvidenceFormatError, decode_evidence, encode_evidence
from archive.feasibility import NoExplanation, has_explanation
from archive.schema import SchemaError, ensure_schema


class ArchiveError(RuntimeError):
    """无法开启、读取或写入采样归档。"""


class SqliteArchive:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection: sqlite3.Connection | None = connection

    @classmethod
    def open(cls, path: str | Path) -> 'SqliteArchive':
        try:
            connection = sqlite3.connect(path, isolation_level=None)
        except (sqlite3.Error, TypeError, ValueError) as exc:
            raise ArchiveError(f'无法打开采样归档 ({exc})') from exc
        try:
            ensure_schema(connection)
        except (SchemaError, sqlite3.Error) as exc:
            connection.close()
            raise ArchiveError(f'无法初始化采样归档 ({exc})') from exc
        return cls(connection)

    def __enter__(self) -> 'SqliteArchive':
        self._open_connection()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def _open_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise ArchiveError('采样归档已关闭')
        return self._connection

    def close(self) -> None:
        if self._connection is not None:
            connection, self._connection = self._connection, None
            connection.close()

    def publish(
        self,
        evidence: CaptureEvidence,
        *,
        before_commit: Callable[[int], None] | None = None,
    ) -> int:
        connection = self._open_connection()
        if connection.in_transaction:
            raise ArchiveError('当前连接已在发布事务中')
        if not isinstance(evidence, CaptureEvidence):
            raise ArchiveError('发布需要完整封存的采样证据')
        if not has_explanation(evidence):
            raise NoExplanation('没有源状态能在全批至多失配一帧')
        payload = encode_evidence(evidence)
        try:
            with connection:
                connection.execute('BEGIN IMMEDIATE')
                cursor = connection.execute('INSERT INTO captures (evidence) VALUES (?)', (payload,))
                candidate_id = cursor.lastrowid
                cursor.close()
                if candidate_id is None:
                    raise ArchiveError('归档未分配候选成果 ID')
                if before_commit is not None:
                    try:
                        before_commit(candidate_id)
                    except Exception as exc:
                        raise ArchiveError(f'预提交检查失败 ({exc})') from exc
        except sqlite3.Error as exc:
            raise ArchiveError(f'成果写入或提交失败 ({exc})') from exc
        return candidate_id

    def get(self, capture_id: int) -> FrozenCapture:
        if type(capture_id) is not int or not 0 < capture_id < (1 << 63):
            raise CaptureNotFound(capture_id)
        connection = self._open_connection()
        if connection.in_transaction:
            raise ArchiveError('请用独立连接读取已提交成果')
        try:
            cursor = connection.execute('SELECT evidence FROM captures WHERE capture_id = ?', (capture_id,))
            try:
                row = cursor.fetchone()
            finally:
                cursor.close()
        except sqlite3.Error as exc:
            raise ArchiveError(f'成果 {capture_id} 读取失败 ({exc})') from exc
        if row is None:
            raise CaptureNotFound(capture_id)
        try:
            evidence = decode_evidence(row[0])
        except EvidenceFormatError as exc:
            raise ArchiveError(f'成果 {capture_id} 的证据无效 ({exc})') from exc
        return FrozenCapture(capture_id, evidence)
