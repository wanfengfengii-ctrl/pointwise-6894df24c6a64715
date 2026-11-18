from dataclasses import dataclass


class RecordingError(ValueError):
    def __init__(self, line: int | None, message: str) -> None:
        self.line = line
        prefix = '录制' if line is None else f'录制第 {line} 行'
        super().__init__(f'{prefix} {message}')


def _validate_line(line: int) -> None:
    if type(line) is not int or line < 1:
        raise ValueError('录制行号必须是正整数')


@dataclass(frozen=True, slots=True)
class StartEvent:
    line: int
    key: str
    bit_width: int

    def __post_init__(self) -> None:
        _validate_line(self.line)
        if type(self.key) is not str or not self.key:
            raise ValueError('采样键必须是非空文本')


@dataclass(frozen=True, slots=True)
class FrameEvent:
    line: int
    frame_id: str
    mask: int
    value: int

    def __post_init__(self) -> None:
        _validate_line(self.line)
        if type(self.frame_id) is not str or not self.frame_id:
            raise ValueError('物理帧 ID 必须是非空文本')
        if type(self.mask) is not int or self.mask <= 0:
            raise ValueError('帧掩码必须是非零正整数')
        if type(self.value) is not int or self.value not in (0, 1):
            raise ValueError('读值必须是整数 0 或 1')


@dataclass(frozen=True, slots=True)
class EndEvent:
    line: int

    def __post_init__(self) -> None:
        _validate_line(self.line)


RecordingEvent = StartEvent | FrameEvent | EndEvent
