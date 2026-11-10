"""pytest cases for exercise 3. tests.json runs them as a "python" test, which splits its points
among the cases that pass."""
import pytest

from exercise_03_temperature import celsius_to_fahrenheit


def test_freezing_point() -> None:
    assert celsius_to_fahrenheit(0) == pytest.approx(32)


def test_boiling_point() -> None:
    assert celsius_to_fahrenheit(100) == pytest.approx(212)


def test_minus_forty_is_the_same_on_both_scales() -> None:
    assert celsius_to_fahrenheit(-40) == pytest.approx(-40)
