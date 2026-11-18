from collections.abc import Iterable

from plans.masks import validate_mask


class QueryParameterError(ValueError):
    """质控请求的掩码数量或值无效。"""


def normalize_masks(bit_width: int, masks: Iterable[int]) -> tuple[int, ...]:
    try:
        ordered = tuple(masks)
    except TypeError as exc:
        raise QueryParameterError('需要提供有序查询掩码') from exc
    if len(ordered) not in (1, 2):
        raise QueryParameterError('查询须提供一个或两个掩码')
    try:
        return tuple(validate_mask(mask, bit_width) for mask in ordered)
    except ValueError as exc:
        raise QueryParameterError(str(exc)) from exc
