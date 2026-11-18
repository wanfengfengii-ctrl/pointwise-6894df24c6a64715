MAX_BIT_WIDTH = 64


def validate_bit_width(bit_width: int) -> int:
    if type(bit_width) is not int or not 1 <= bit_width <= MAX_BIT_WIDTH:
        raise ValueError(f'位宽必须是 1～{MAX_BIT_WIDTH} 的整数')
    return bit_width


def validate_mask(mask: int, bit_width: int) -> int:
    width = validate_bit_width(bit_width)
    if type(mask) is not int or not 0 < mask < (1 << width):
        raise ValueError(f'掩码必须是 {width} 位范围内的非零整数')
    return mask
