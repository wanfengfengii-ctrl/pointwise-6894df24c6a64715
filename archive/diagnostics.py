import hashlib
import hmac
import json
from dataclasses import dataclass

from admission.evidence import CaptureEvidence
from plans.catalog import LoopCatalog
from plans.model import SamplingPlan
from recordings.events import FrameEvent


class DiagnosticFormatError(ValueError):
    """持久化的拒绝诊断无法恢复为冻结声明、观测与诊断号的绑定。"""


@dataclass(frozen=True, slots=True)
class FrozenDiagnostic:
    diagnostic_id: str
    evidence: CaptureEvidence


def _binding(diagnostic_id: str, payload: str) -> str:
    return hashlib.sha256(
        diagnostic_id.encode('utf-8') + b'\x00' + payload.encode('utf-8')
    ).hexdigest()


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    body = {}
    for key, value in pairs:
        if key in body:
            raise ValueError(f'诊断证据字段 {key!r} 重复')
        body[key] = value
    return body


def _rows(value: object, size: int, label: str) -> list:
    if type(value) is not list or any(type(row) is not list or len(row) != size for row in value):
        raise ValueError(f'{label}的持久结构无效')
    return value


def encode_diagnostic(diagnostic_id: str, evidence: CaptureEvidence) -> str:
    if type(diagnostic_id) is not str or not diagnostic_id:
        raise DiagnosticFormatError('诊断号必须是非空文本')
    plan = evidence.plan
    return json.dumps(
        {
            'diagnostic_id': diagnostic_id,
            'plan': {'key': plan.key, 'bit_width': plan.bit_width, 'loops': plan.catalog.items()},
            'frames': [(frame.line, frame.frame_id, frame.mask, frame.value) for frame in evidence.frames],
        },
        ensure_ascii=True,
        separators=(',', ':'),
    )


def bind(diagnostic_id: str, payload: str) -> str:
    return _binding(diagnostic_id, payload)


def decode_diagnostic(diagnostic_id: str, payload: object, binding: object) -> FrozenDiagnostic:
    """按诊断号与固定摘要恢复证据；任一处不符即视为损坏。"""
    try:
        if type(diagnostic_id) is not str or not diagnostic_id:
            raise ValueError('诊断号必须是非空文本')
        if type(payload) is not str:
            raise ValueError('持久诊断必须为文本')
        if type(binding) is not str or not hmac.compare_digest(binding, _binding(diagnostic_id, payload)):
            raise ValueError('诊断证据与诊断号的绑定不符')
        body = json.loads(payload, object_pairs_hook=_unique_object)
        if type(body) is not dict or type(body.get('plan')) is not dict:
            raise ValueError('缺少持久诊断声明')
        if body.get('diagnostic_id') != diagnostic_id:
            raise ValueError('诊断证据内嵌的号与请求不符')
        declaration = body['plan']
        width = declaration['bit_width']
        catalog = LoopCatalog(width, tuple(_rows(declaration['loops'], 2, '回路表')))
        plan = SamplingPlan(declaration['key'], width, catalog)
        frames = tuple(FrameEvent(*row) for row in _rows(body['frames'], 4, '帧记录'))
        evidence = CaptureEvidence(plan, frames)
        return FrozenDiagnostic(diagnostic_id, evidence)
    except (KeyError, TypeError, ValueError, RecursionError) as exc:
        raise DiagnosticFormatError(f'固定诊断无法恢复 ({exc})') from exc
