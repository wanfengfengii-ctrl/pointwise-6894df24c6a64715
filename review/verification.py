from dataclasses import dataclass

from archive.artifacts import CaptureNotFound, DiagnosticNotFound
from archive.certificate import validate_certificate
from archive.storage import ArchiveError, SqliteArchive
from review.export import HandoverRecord
from review.parameters import QueryParameterError
from review.query import query_capture


@dataclass(frozen=True, slots=True)
class VerificationResult:
    mismatches: tuple[str, ...]
    detail: str | None = None

    @property
    def matches(self) -> bool:
        return not self.mismatches


def _integer_tuple(value: object) -> bool:
    return type(value) is tuple and all(type(item) is int for item in value)


def verify_export(
    archive: SqliteArchive,
    record: HandoverRecord,
    expected_capture_id: int,
    *expected_masks: int,
) -> VerificationResult:
    """按交接约定重算，并定位与固定成果不一致的记录字段。"""
    if not isinstance(record, HandoverRecord):
        return VerificationResult(('record',), '需要交接记录')
    try:
        frozen = archive.get(expected_capture_id)
        actual = tuple(query_capture(frozen, *expected_masks))
    except QueryParameterError as exc:
        return VerificationResult(('masks',), str(exc))
    except (ArchiveError, CaptureNotFound) as exc:
        return VerificationResult(('capture',), str(exc))

    mismatches = []
    if type(record.capture_id) is not int or record.capture_id != frozen.capture_id:
        mismatches.append('capture_id')
    if record.sampling_key != frozen.plan.key:
        mismatches.append('sampling_key')
    if type(record.bit_width) is not int or record.bit_width != frozen.plan.bit_width:
        mismatches.append('bit_width')
    if not _integer_tuple(record.masks) or record.masks != expected_masks:
        mismatches.append('masks')
    if (
        type(record.combinations) is not tuple
        or not all(_integer_tuple(row) for row in record.combinations)
        or record.combinations != actual
    ):
        mismatches.append('combinations')
    return VerificationResult(tuple(mismatches))


def _certificate_groups(value: object) -> object:
    return value if type(value) in (list, tuple) else None


def verify_rejection(archive: SqliteArchive, record: object, expected_diagnostic_id: str) -> bool:
    """按指定诊断号读取冻结证据，独立验算任一有效矛盾证书。"""
    if type(record) is not dict:
        return False
    if type(expected_diagnostic_id) is not str or not expected_diagnostic_id:
        return False
    try:
        frozen = archive.get_diagnostic(expected_diagnostic_id)
    except (ArchiveError, DiagnosticNotFound):
        return False
    if record.get('diagnostic_id') != expected_diagnostic_id or frozen.diagnostic_id != expected_diagnostic_id:
        return False
    groups = _certificate_groups(record.get('groups'))
    if groups is None:
        return False
    return validate_certificate(frozen.evidence.frames, groups)
