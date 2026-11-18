import sqlite3
import uuid
from collections.abc import Callable, Iterable

from admission.capture import collect_evidence
from admission.frames import FrameConflict
from admission.session import SessionError
from archive.feasibility import NoExplanation
from archive.storage import ArchiveError, SqliteArchive
from plans.model import SamplingPlan
from recordings.events import RecordingEvent


class CaptureRejected(RuntimeError):
    """采样证据未能通过接纳、资格裁决或发布。"""

    def __init__(self, message: str, *, diagnostic_id: str | None = None) -> None:
        super().__init__(message)
        self.diagnostic_id = diagnostic_id


def _new_diagnostic_id() -> str:
    return uuid.uuid4().hex


def capture(
    archive: SqliteArchive,
    plan: SamplingPlan,
    events: Iterable[RecordingEvent],
    *,
    before_commit: Callable[[int], None] | None = None,
) -> int:
    """接纳整批录制并返回已提交成果的 ID。"""
    try:
        evidence = collect_evidence(plan, events)
    except (FrameConflict, SessionError) as exc:
        raise CaptureRejected(str(exc)) from exc

    try:
        return archive.publish(evidence, before_commit=before_commit)
    except NoExplanation as exc:
        # 仅完整录制在至多一次失配下无解时留存诊断；诊断失败不发号。
        diagnostic_id = _publish_diagnostic(archive, evidence)
        raise CaptureRejected(str(exc), diagnostic_id=diagnostic_id) from exc
    except ArchiveError as exc:
        raise CaptureRejected(str(exc)) from exc


def _publish_diagnostic(archive: SqliteArchive, evidence) -> str | None:
    """留存诊断；任何写入或提交失败都不给出诊断号。"""
    for _ in range(4):
        candidate = _new_diagnostic_id()
        try:
            archive.publish_diagnostic(candidate, evidence)
        except Exception as exc:
            if isinstance(exc.__cause__, sqlite3.IntegrityError):
                # 诊断号撞号时换一个号重试；其余失败不发号。
                continue
            return None
        else:
            return candidate
    return None
