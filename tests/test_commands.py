"""Struktur der Slash-Commands und reine Hilfsfunktionen (ohne Discord-Verbindung)."""

from datetime import UTC, datetime

import pytest

from vinted_bot.discord_bot.channels import channel_slug
from vinted_bot.discord_bot.commands import FilterCommands, describe_filter
from vinted_bot.storage.repository import FilterRow


def make_filter(**overrides: object) -> FilterRow:
  values: dict[str, object] = {
    "id": 3,
    "name": "rl-jacken",
    "source_url": "x",
    "canonical_url": "x",
    "domain": "at",
    "api_params": {},
    "channel_id": 555,
    "countries": ("AT", "DE"),
    "interval_s": 60,
    "paused": False,
    "baseline_done": True,
    "max_seen_id": None,
    "last_success_at": datetime(2026, 10, 15, 12, 0, tzinfo=UTC),
  }
  values.update(overrides)
  return FilterRow(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
  ("name", "slug"),
  [
    ("Ralph Lauren Jacken (AT)", "ralph-lauren-jacken-at"),
    ("Größe M – Pullöver", "groesse-m-pulloever"),
    ("  !!!  ", "vinted-filter"),
    ("x" * 150, "x" * 100),
  ],
)
def test_channel_slug(name: str, slug: str) -> None:
  assert channel_slug(name) == slug


def test_describe_active_filter() -> None:
  text = describe_filter(make_filter())
  assert "**#3 rl-jacken**" in text
  assert "<#555>" in text
  assert "🇦🇹 🇩🇪" in text
  assert "▶️ aktiv" in text
  assert "<t:" in text


def test_describe_paused_and_failing() -> None:
  assert "⏸️ pausiert" in describe_filter(make_filter(paused=True, countries=()))
  assert "🌍 alle" in describe_filter(make_filter(countries=()))
  assert "⚠️ 3 Fehler" in describe_filter(make_filter(fail_count=3))
  assert "noch nie" in describe_filter(make_filter(last_success_at=None))


def test_command_tree_structure() -> None:
  group = FilterCommands(bot=None, service=None, repo=None)  # type: ignore[arg-type]
  names = sorted(command.name for command in group.commands)
  assert names == ["add", "galerie", "intervall", "laender", "list", "pause", "remove", "resume"]
  assert group.guild_only
  assert group.default_permissions is not None and group.default_permissions.administrator

  add = group.get_command("add")
  assert add is not None
  params = {p.name: p.required for p in add.parameters}  # type: ignore[union-attr]
  assert params == {
    "link": True,
    "name": False,
    "laender": False,
    "intervall": False,
    "kanal": False,
    "galerie": False,
  }

  for name in ("pause", "resume", "remove", "laender", "intervall", "galerie"):
    command = group.get_command(name)
    filter_param = next(p for p in command.parameters if p.name == "filter")  # type: ignore[union-attr]
    assert filter_param.autocomplete, f"/filter {name} braucht Autovervollständigung"
