import pytest

from vinted_bot.vinted.countries import (
  InvalidCountryCodeError,
  flag_emoji,
  normalize_country,
  parse_country_codes,
)


@pytest.mark.parametrize(
  ("text", "expected"),
  [
    ("at,de,it,fr", ("AT", "DE", "IT", "FR")),
    (" AT ; de  it ", ("AT", "DE", "IT")),
    ("de,DE,de", ("DE",)),
    ("uk", ("GB",)),
    ("", ()),
    (None, ()),
  ],
)
def test_parse_country_codes(text: str | None, expected: tuple[str, ...]) -> None:
  assert parse_country_codes(text) == expected


def test_unknown_codes_are_all_reported() -> None:
  with pytest.raises(InvalidCountryCodeError) as info:
    parse_country_codes("at,xx,ch")
  assert "xx" in str(info.value) and "ch" in str(info.value)


def test_flags() -> None:
  assert flag_emoji("AT") == "🇦🇹"
  assert flag_emoji("de") == "🇩🇪"
  assert flag_emoji("UK") == "🇬🇧"
  assert flag_emoji(None) == "❔"
  assert flag_emoji("123") == "❔"


def test_normalize() -> None:
  assert normalize_country(" fr ") == "FR"
  assert normalize_country("") is None
