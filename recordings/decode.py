import json

from plans.masks import MAX_BIT_WIDTH, validate_bit_width, validate_mask
from recordings.events import EndEvent, FrameEvent, RecordingError, RecordingEvent, StartEvent


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'JSON 字段 {key!r} 重复')
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError(f'JSON 常量 {value!r} 无效')


def decode_event(line: int, text: str) -> RecordingEvent:
    try:
        body = json.loads(text, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
        if type(body) is not dict:
            raise ValueError('事件必须是 JSON 对象')
        kind = body['type']
        if kind == 'start':
            return StartEvent(line, body['key'], validate_bit_width(body['bit_width']))
        if kind == 'frame':
            mask = validate_mask(body['mask'], MAX_BIT_WIDTH)
            return FrameEvent(line, body['frame_id'], mask, body['value'])
        if kind == 'end':
            return EndEvent(line)
        raise ValueError('未知的事件类型')
    except KeyError as exc:
        raise RecordingError(line, f'缺少字段 {exc.args[0]!r}') from exc
    except (ValueError, RecursionError) as exc:
        raise RecordingError(line, f'事件无效 ({exc})') from exc
