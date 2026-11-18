from enum import Enum, auto

from admission.evidence import CaptureEvidence
from admission.frames import AdmissionOutcome, FrameConflict, FrameLedger
from plans.masks import validate_bit_width, validate_mask
from plans.model import SamplingPlan
from recordings.events import EndEvent, FrameEvent, RecordingError, StartEvent


class SessionState(Enum):
    ACCEPTING = auto()
    FINISHED = auto()
    REJECTED = auto()
    ABORTED = auto()


class SessionError(RuntimeError):
    """采样声明不匹配或会话状态不允许当前操作。"""


class CaptureSession:
    def __init__(self, plan: SamplingPlan, event: StartEvent) -> None:
        if not isinstance(event, StartEvent):
            raise RecordingError(None, '会话需要开始事件')
        try:
            validate_bit_width(event.bit_width)
        except ValueError as exc:
            raise RecordingError(event.line, str(exc)) from exc
        if not isinstance(plan, SamplingPlan) or not plan.matches_start(event.key, event.bit_width):
            raise SessionError(f'第 {event.line} 行的开始事件与所选采样声明不符')
        self._plan = plan
        self._ledger = FrameLedger()
        self._state = SessionState.ACCEPTING

    @classmethod
    def start(cls, plan: SamplingPlan, event: StartEvent) -> 'CaptureSession':
        return cls(plan, event)

    @property
    def state(self) -> SessionState:
        return self._state

    def _require_accepting(self) -> None:
        if self._state is not SessionState.ACCEPTING:
            raise SessionError(f'会话状态 {self._state.name} 不接受新事件')

    def accept_frame(self, frame: FrameEvent) -> AdmissionOutcome:
        self._require_accepting()
        if not isinstance(frame, FrameEvent):
            self.abort()
            raise RecordingError(None, '会话需要帧事件')
        try:
            validate_mask(frame.mask, self._plan.bit_width)
        except ValueError as exc:
            self.abort()
            raise RecordingError(frame.line, str(exc)) from exc
        try:
            return self._ledger.observe(frame)
        except FrameConflict:
            self._state = SessionState.REJECTED
            raise

    def finish(self, event: EndEvent) -> CaptureEvidence:
        self._require_accepting()
        if not isinstance(event, EndEvent):
            self.abort()
            raise RecordingError(None, '会话需要结束事件才能封存')
        evidence = CaptureEvidence(self._plan, self._ledger.observations())
        self._state = SessionState.FINISHED
        return evidence

    def abort(self) -> None:
        if self._state is SessionState.ACCEPTING:
            self._state = SessionState.ABORTED
