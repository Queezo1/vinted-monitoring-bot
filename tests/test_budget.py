"""Budget-Verteilung nach Priorität (core/budget.py)."""

import pytest

from vinted_bot.core.budget import effective_intervals

SPACING = 3.0  # 20 Anfragen/min insgesamt, davon 1/1,2 für Katalog = alle 3,6 s eine


def test_everything_fits_no_stretch() -> None:
  assert effective_intervals({1: 10, 2: 120}, SPACING) == {1: 10, 2: 120}


def test_equal_weights_stretch_equally() -> None:
  # 6 Filter à 10 s wollen 0,6/s, Budget 0,278/s -> jeder ~21,6 s
  result = effective_intervals({i: 10 for i in range(6)}, SPACING)
  assert all(value == pytest.approx(21.6) for value in result.values())


def test_priorities_two_top_filters_stay_fast() -> None:
  # Deine Einstellung: SI Pullover + SI Jacken auf 10 s, die anderen 4 auf 120 s
  result = effective_intervals({3: 10, 4: 10, 1: 120, 2: 120, 5: 120, 6: 120}, SPACING)
  assert result[3] == result[4] == pytest.approx(10.0)  # passt ins Budget
  assert result[1] == pytest.approx(120.0)


def test_turbo_two_filters_at_five_seconds() -> None:
  # Deine Turbo-Einstellung: Abstand 2 s (30/min), SI Pullover + Jacken 5 s, RL 120 s, Shirts 600 s
  result = effective_intervals({3: 5, 4: 5, 1: 120, 2: 120, 5: 600}, 2.0)
  assert result[3] == result[4] == pytest.approx(5.02, abs=0.01)


def test_slower_spacing_after_throttling_stretches_everyone() -> None:
  normal = effective_intervals({1: 10, 2: 10}, SPACING)
  throttled = effective_intervals({1: 10, 2: 10}, SPACING * 1.5)
  assert throttled[1] > normal[1]


def test_empty() -> None:
  assert effective_intervals({}, SPACING) == {}
