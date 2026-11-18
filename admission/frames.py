from enum import Enum, auto

from recordings.events import FrameEvent


class AdmissionOutcome(Enum):
    ACCEPTED = auto()
    REPLAYED = auto()


class FrameConflict(ValueError):
    def __init__(self, previous: FrameEvent, incoming: FrameEvent) -> None:
        self.previous = previous
        self.incoming = incoming
        super().__init__(
            f'物理帧 {incoming.frame_id!r} 在第 {previous.line} 行和'
            f'第 {incoming.line} 行的内容冲突'
        )


class FrameLedger:
    def __init__(self) -> None:
        self._by_id: dict[str, FrameEvent] = {}

    def observe(self, frame: FrameEvent) -> AdmissionOutcome:
        previous = self._by_id.get(frame.frame_id)
        if previous is None:
            self._by_id[frame.frame_id] = frame
            return AdmissionOutcome.ACCEPTED
        if (previous.mask, previous.value) != (frame.mask, frame.value):
            raise FrameConflict(previous, frame)
        return AdmissionOutcome.REPLAYED

    def observations(self) -> tuple[FrameEvent, ...]:
        return tuple(self._by_id.values())
