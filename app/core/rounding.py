# PHP's round() is round-half-away-from-zero (round(0.125, 2) === 0.13 in PHP),
# but Python's built-in round() is round-half-to-even/banker's rounding
# (round(0.125, 2) == 0.12). The BFSI calculator (bfsi-calculator's scoring.php) is
# PHP and uses PHP's round() on score values, so the BFSI port needs this helper
# instead of the builtin round() to match production output exactly.
#
# php_round is for the BFSI port ONLY. The ESG calculator (esg_score_calculator-master)
# is Python and uses the built-in round() (e.g. utils/helper.py:92), so the ESG port
# keeps the built-in round(). See docs/analysis/bfsi.md and docs/analysis/esg.md.
import decimal


def php_round(value: float, ndigits: int = 2) -> float:
    quantum = decimal.Decimal("1." + "0" * ndigits)
    # Production BFSI runs PHP 8.2, and PHP < 8.4's round() pre-rounds the value to 15
    # significant digits before rounding, so 65.15499999999999 rounds to 65.16 there.
    # format(value, ".15g") reproduces that pre-rounding; it also (like str(value), and
    # unlike Decimal(value) directly) keeps binary-float representation error from
    # skewing which side of the tie a value like 0.125 lands on.
    return float(
        decimal.Decimal(format(value, ".15g")).quantize(quantum, rounding=decimal.ROUND_HALF_UP)
    )
