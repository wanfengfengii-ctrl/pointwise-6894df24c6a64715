from collections.abc import Iterable

from admission.evidence import CaptureEvidence
from admission.session import CaptureSession
from plans.model import SamplingPlan
from recordings.events import EndEvent, FrameEvent, RecordingError, RecordingEvent, StartEvent


def collect_evidence(plan: SamplingPlan, events: Iterable[RecordingEvent]) -> CaptureEvidence:
    """驱动单批会话，直到完整结束后交付证据。"""
    try:
        stream = iter(events)
    except TypeError as exc:
        raise RecordingError(1, '缺少可读取的事件流') from exc

    start = next(stream, None)
    if not isinstance(start, StartEvent):
        raise RecordingError(getattr(start, 'line', 1), '缺少有效开始事件')
    session = CaptureSession.start(plan, start)
    last_line = start.line
    end_of_stream = object()
    try:
        for event in stream:
            line = getattr(event, 'line', last_line + 1)
            if isinstance(event, EndEvent):
                trailing = next(stream, end_of_stream)
                if trailing is not end_of_stream:
                    raise RecordingError(getattr(trailing, 'line', line + 1), '结束事件后存在多余事件')
                return session.finish(event)
            if not isinstance(event, FrameEvent):
                raise RecordingError(line, '采样内部需要帧事件或结束事件')
            session.accept_frame(event)
            last_line = line
        raise RecordingError(last_line + 1, '事件流缺少结束事件')
    except RecordingError:
        session.abort()
        raise
