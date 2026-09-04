"""金额换算。一律 Decimal，绝不用 float 算钱。

取自 sub2api 的两个独立旋钮：
- 到账倍率：充 100 到账 110 → multiplier=1.1，结果四舍五入到分；
- 手续费率：百分比，手续费**向上**取整到分再加到实付。
"""

from decimal import ROUND_HALF_UP, ROUND_UP, Decimal

CENT = Decimal("0.01")
# 网关回传金额与订单实付允许的最大偏差（两位小数币种）
AMOUNT_TOLERANCE = CENT


def quantize_money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def normalize_multiplier(multiplier: Decimal) -> Decimal:
    return multiplier if multiplier > 0 else Decimal("1")


def calculate_credited_balance(pay_amount: Decimal, multiplier: Decimal) -> Decimal:
    """用户实付 pay_amount 后应到账多少余额。"""
    return quantize_money(pay_amount * normalize_multiplier(multiplier))


def calculate_pay_amount(amount: Decimal, fee_rate_percent: Decimal) -> Decimal:
    """用户想充 amount，加上手续费后网关应收多少。"""
    if fee_rate_percent <= 0:
        return quantize_money(amount)
    fee = (amount * fee_rate_percent / Decimal("100")).quantize(CENT, rounding=ROUND_UP)
    return quantize_money(amount + fee)


def amounts_match(expected: Decimal, paid: Decimal) -> bool:
    return abs(expected - paid) <= AMOUNT_TOLERANCE


def has_valid_scale(amount: Decimal) -> bool:
    """拒绝超过两位小数的金额，而不是悄悄四舍五入。"""
    return amount == quantize_money(amount)
