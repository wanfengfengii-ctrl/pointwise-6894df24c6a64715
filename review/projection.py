from itertools import product

from admission.evidence import CaptureEvidence
from archive.feasibility import has_explanation


def project_values(evidence: CaptureEvidence, masks: tuple[int, ...]) -> list[tuple[int, ...]]:
    """按调用方校验过的掩码顺序投影全部共同可行的组合。"""
    combinations = []
    for values in product((0, 1), repeat=len(masks)):
        hard = tuple(zip(masks, values, strict=True))
        if has_explanation(evidence, hard):
            combinations.append(values)
    return combinations
