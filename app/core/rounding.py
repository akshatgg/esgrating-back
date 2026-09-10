# PHP's round() is round-half-away-from-zero (round(0.125, 2) === 0.13 in PHP),
# but Python's built-in round() is round-half-to-even/banker's rounding
# (round(0.125, 2) == 0.12). The original PHP sources (bfsi-calculator's
# scoring.php, esg_score_calculator-master's helper.py which itself calls PHP-style
# round semantics) use PHP's round() on score values, so ports that replicate those
# call sites need this helper instead of the builtin round() to match production
# output exactly. See docs/analysis/bfsi.md and docs/analysis/esg.md.
import decimal


def php_round(value: float, ndigits: int = 2) -> float:
    quantum = decimal.Decimal("1." + "0" * ndigits)
    # str(value) (not Decimal(value) directly) avoids binary-float representation
    # error skewing which side of the tie a value like 0.125 lands on.
    return float(
        decimal.Decimal(str(value)).quantize(quantum, rounding=decimal.ROUND_HALF_UP)
    )
