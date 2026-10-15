from tests.conftest import make_item
from vinted_bot.core.dedupe import select_new


def test_first_run_only_sets_baseline() -> None:
  items = [make_item(3), make_item(2), make_item(1)]
  result = select_new(items, set(), baseline_done=False, max_seen_id=None)
  assert result.baseline_ids == [3, 2, 1]
  assert result.new_items == []
  assert result.max_seen_id == 3


def test_only_unseen_items_are_new_and_oldest_comes_first() -> None:
  items = [make_item(12), make_item(11), make_item(10)]
  result = select_new(items, {10}, baseline_done=True, max_seen_id=10)
  assert [item.id for item in result.new_items] == [11, 12]
  assert result.skipped_ids == []
  assert result.max_seen_id == 12


def test_promoted_items_are_skipped() -> None:
  items = [make_item(12, promoted=True), make_item(11)]
  result = select_new(items, set(), baseline_done=True, max_seen_id=10)
  assert [item.id for item in result.new_items] == [11]
  assert result.skipped_ids == [12]


def test_ids_far_below_watermark_are_skipped() -> None:
  items = [make_item(1_000_000), make_item(500)]
  result = select_new(items, set(), baseline_done=True, max_seen_id=1_000_000, id_tolerance=1000)
  assert [item.id for item in result.new_items] == [1_000_000]
  assert result.skipped_ids == [500]


def test_ids_slightly_below_watermark_are_still_new() -> None:
  # IDs sind in "newest_first" nicht streng sortiert -> kleine Abweichungen sind normal
  result = select_new(
    [make_item(990)], set(), baseline_done=True, max_seen_id=1000, id_tolerance=50
  )
  assert [item.id for item in result.new_items] == [990]


def test_flood_is_capped_to_newest() -> None:
  items = [make_item(i) for i in range(30, 0, -1)]  # 30 neue, neueste zuerst
  result = select_new(items, set(), baseline_done=True, max_seen_id=0, max_posts=10)
  assert [item.id for item in result.new_items] == list(range(21, 31))
  assert sorted(result.skipped_ids) == list(range(1, 21))


def test_empty_result() -> None:
  result = select_new([], {1}, baseline_done=True, max_seen_id=5)
  assert result.new_items == [] and result.max_seen_id == 5
