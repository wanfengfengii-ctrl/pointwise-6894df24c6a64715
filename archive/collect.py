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
        return archive.publish(evidence, before_commit=before_commit)
    except (FrameConflict, SessionError, NoExplanation, ArchiveError) as exc:
        raise CaptureRejected(str(exc)) from exc
