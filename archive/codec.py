import json

from admission.evidence import CaptureEvidence
from plans.catalog import LoopCatalog
from plans.model import SamplingPlan
from recordings.events import FrameEvent


class EvidenceFormatError(ValueError):
    """持久化的采样证据无法解释为冻结声明及观测。"""


def encode_evidence(evidence: CaptureEvidence) -> str:
    plan = evidence.plan
    return json.dumps(
        {
            'plan': {'key': plan.key, 'bit_width': plan.bit_width, 'loops': plan.catalog.items()},
            'frames': [(frame.line, frame.frame_id, frame.mask, frame.value) for frame in evidence.frames],
        },
        ensure_ascii=True,
        separators=(',', ':'),
    )


def _rows(value: object, size: int, label: str) -> list:
    if type(value) is not list or any(type(row) is not list or len(row) != size for row in value):
        raise ValueError(f'{label}的持久结构无效')
    return value


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    body = {}
    for key, value in pairs:
        if key in body:
            raise ValueError(f'持久证据字段 {key!r} 重复')
        body[key] = value
    return body


def decode_evidence(payload: str) -> CaptureEvidence:
    try:
        if type(payload) is not str:
            raise ValueError('持久证据必须为文本')
        body = json.loads(payload, object_pairs_hook=_unique_object)
        if type(body) is not dict or type(body.get('plan')) is not dict:
            raise ValueError('缺少持久证据声明')
        declaration = body['plan']
        width = declaration['bit_width']
        catalog = LoopCatalog(width, tuple(_rows(declaration['loops'], 2, '回路表')))
        plan = SamplingPlan(declaration['key'], width, catalog)
        frames = tuple(FrameEvent(*row) for row in _rows(body['frames'], 4, '帧记录'))
        return CaptureEvidence(plan, frames)
    except (KeyError, TypeError, ValueError, RecursionError) as exc:
        raise EvidenceFormatError(f'固定证据无法恢复 ({exc})') from exc
