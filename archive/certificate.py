from collections.abc import Iterable, Sequence

from recordings.events import FrameEvent


class CertificateError(ValueError):
    """冻结证据无法支持一份矛盾证书。"""


def _witness(frames: Sequence[FrameEvent], skip: int | None) -> frozenset[int] | None:
    """在省略 skip 位置的帧后，找出掩码异或为零而读值异或为一的物理帧子集。"""
    basis: dict[int, tuple[int, int, frozenset[int]]] = {}
    for index, frame in enumerate(frames):
        if index == skip:
            continue
        mask, value, provenance = frame.mask, frame.value, frozenset((index,))
        while mask:
            pivot = mask.bit_length() - 1
            row = basis.get(pivot)
            if row is None:
                basis[pivot] = (mask, value, provenance)
                break
            row_mask, row_value, row_provenance = row
            mask ^= row_mask
            value ^= row_value
            provenance ^= row_provenance
        else:
            if value:
                return provenance
    return None


def build_certificate(frames: Iterable[FrameEvent]) -> tuple[tuple[str, ...], ...]:
    """为无解的一批独立物理帧构造矛盾证书，各组总交集为空。"""
    sealed = tuple(frames)
    witness = _witness(sealed, None)
    if witness is None:
        raise CertificateError('整批在全批至多失配一次下有解')
    groups = [witness]
    common = set(witness)
    while common:
        index = min(common)
        # 全批在至多一次失配下无解，意味着去掉任一帧后仍无解，必有其子集见证。
        avoided = _witness(sealed, index)
        if avoided is None:
            raise CertificateError(f'物理帧 {sealed[index].frame_id!r} 缺少独立矛盾见证')
        groups.append(avoided)
        common &= avoided
    return tuple(
        tuple(sealed[index].frame_id for index in sorted(group)) for group in groups
    )


def validate_certificate(frames: Iterable[FrameEvent], groups: object) -> bool:
    """逐组核对任意证书：帧身份、组内唯一、异或条件及总交集。"""
    if type(groups) not in (list, tuple) or not groups:
        return False
    by_id = {frame.frame_id: frame for frame in frames}
    common: set[str] | None = None
    for group in groups:
        if type(group) not in (list, tuple) or not group:
            return False
        seen: set[str] = set()
        mask = 0
        value = 0
        for frame_id in group:
            if type(frame_id) is not str or not frame_id:
                return False
            if frame_id in seen:
                return False
            frame = by_id.get(frame_id)
            if frame is None:
                return False
            seen.add(frame_id)
            mask ^= frame.mask
            value ^= frame.value
        if mask != 0 or value != 1:
            return False
        common = seen if common is None else common & seen
    return common is not None and not common
