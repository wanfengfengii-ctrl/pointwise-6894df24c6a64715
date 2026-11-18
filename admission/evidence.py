from dataclasses import dataclass

from plans.masks import validate_mask
from plans.model import SamplingPlan
from recordings.events import FrameEvent


@dataclass(frozen=True, slots=True)
class CaptureEvidence:
    plan: SamplingPlan
    frames: tuple[FrameEvent, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.plan, SamplingPlan):
            raise ValueError('封存证据缺少有效采样声明')
        frames = tuple(self.frames)
        seen_ids = set()
        for frame in frames:
            if not isinstance(frame, FrameEvent):
                raise ValueError('封存证据含非法帧对象')
            validate_mask(frame.mask, self.plan.bit_width)
            if frame.frame_id in seen_ids:
                raise ValueError(f'封存证据含重复物理帧 ID {frame.frame_id!r}')
            seen_ids.add(frame.frame_id)
        object.__setattr__(self, 'frames', frames)
