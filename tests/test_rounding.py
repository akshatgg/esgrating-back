from app.core.rounding import php_round


def test_half_up_positive_tie():
    # PHP: round(0.125, 2) === 0.13 (Python's builtin round(0.125, 2) == 0.12)
    assert php_round(0.125, 2) == 0.13


def test_half_up_positive_tie_other_digit():
    assert php_round(0.135, 2) == 0.14


def test_whole_number_unchanged():
    assert php_round(70.0, 2) == 70.0


def test_half_up_negative_tie():
    # PHP rounds half away from zero in both directions.
    assert php_round(-0.125, 2) == -0.13


def test_normal_rounding_matches_python_round():
    assert php_round(1.234, 2) == round(1.234, 2) == 1.23


def test_normal_rounding_matches_python_round_up():
    assert php_round(1.236, 2) == round(1.236, 2) == 1.24
