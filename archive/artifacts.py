from dataclasses import dataclass

from admission.evidence import CaptureEvidence
from plans.model import SamplingPlan


class CaptureNotFound(LookupError):
    def __init__(self, capture_id: object) -> None:
        self.capture_id = capture_id
        super().__init__(f'成果 ID {capture_id!r} 不可用')


class DiagnosticNotFound(LookupError):
    def __init__(self, diagnostic_id: object) -> None:
        self.diagnostic_id = diagnostic_id
        super().__init__(f'诊断号 {diagnostic_id!r} 不可用')


@dataclass(frozen=True, slots=True)
class FrozenCapture:
    capture_id: int
    evidence: CaptureEvidence

    @property
    def plan(self) -> SamplingPlan:
        return self.evidence.plan
