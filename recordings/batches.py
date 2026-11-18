from collections.abc import Iterator
from contextlib import closing

from plans.masks import validate_mask
from recordings.decode import decode_event
from recordings.events import EndEvent, RecordingError, RecordingEvent, StartEvent
from recordings.source import RecordingSource


def read_recording(source: RecordingSource) -> Iterator[RecordingEvent]:
    """依次交付事件，确认录制末尾完整后交付结束事件。"""
    with closing(source.iter_lines()) as lines:
        first = next(lines, None)
        if first is None:
            raise RecordingError(1, '缺少开始事件')
        start = decode_event(*first)
        if not isinstance(start, StartEvent):
            raise RecordingError(start.line, '首个事件必须是开始')
        yield start

        last_line = start.line
        for line, text in lines:
            last_line = line
            event = decode_event(line, text)
            if isinstance(event, StartEvent):
                raise RecordingError(line, '批内出现第二个开始事件')
            if isinstance(event, EndEvent):
                trailing = next(lines, None)
                if trailing is not None:
                    raise RecordingError(trailing[0], '结束事件后存在多余输入')
                yield event
                return
            try:
                validate_mask(event.mask, start.bit_width)
            except ValueError as exc:
                raise RecordingError(line, str(exc)) from exc
            yield event

        raise RecordingError(last_line + 1, '文件耗尽，缺少显式结束事件')
