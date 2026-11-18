from dataclasses import dataclass

from archive.artifacts import CaptureNotFound, DiagnosticNotFound
from archive.certificate import CertificateError, build_certificate
from archive.storage import ArchiveError, SqliteArchive
from review.parameters import QueryParameterError
from review.query import query_capture


class ExportError(RuntimeError):
    """无法按选定成果及查询参数生成交接记录。"""


@dataclass(frozen=True, slots=True)
class HandoverRecord:
    capture_id: int
    sampling_key: str
    bit_width: int
    masks: tuple[int, ...]
    combinations: tuple[tuple[int, ...], ...]

    def as_dict(self) -> dict[str, object]:
        return {
            'capture_id': self.capture_id,
            'sampling_key': self.sampling_key,
            'bit_width': self.bit_width,
            'masks': list(self.masks),
            'combinations': [list(values) for values in self.combinations],
        }


def export(archive: SqliteArchive, capture_id: int, *masks: int) -> HandoverRecord:
    try:
        frozen = archive.get(capture_id)
        combinations = tuple(query_capture(frozen, *masks))
    except (ArchiveError, CaptureNotFound, QueryParameterError) as exc:
        raise ExportError(f'交接记录无法生成 ({exc})') from exc
    return HandoverRecord(frozen.capture_id, frozen.plan.key, frozen.plan.bit_width, masks, combinations)


def export_rejection(archive: SqliteArchive, diagnostic_id: str) -> dict[str, object]:
    """按已提交诊断导出由物理帧 ID 组成的矛盾证书。"""
    try:
        frozen = archive.get_diagnostic(diagnostic_id)
        groups = build_certificate(frozen.evidence.frames)
    except (ArchiveError, DiagnosticNotFound, CertificateError) as exc:
        raise ExportError(f'拒绝诊断无法导出 ({exc})') from exc
    return {'diagnostic_id': frozen.diagnostic_id, 'groups': [list(group) for group in groups]}
