from archive.artifacts import FrozenCapture
from archive.storage import ArchiveError, SqliteArchive
from review.parameters import normalize_masks
from review.projection import project_values


def query_capture(frozen: FrozenCapture, *masks: int) -> list[tuple[int, ...]]:
    ordered = normalize_masks(frozen.plan.bit_width, masks)
    values = project_values(frozen.evidence, ordered)
    if not values:
        raise ArchiveError(f'成果 {frozen.capture_id} 的证据没有可解释状态')
    return values


def query(archive: SqliteArchive, capture_id: int, *masks: int) -> list[tuple[int, ...]]:
    return query_capture(archive.get(capture_id), *masks)
