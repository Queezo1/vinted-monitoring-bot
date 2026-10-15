"""Länderkürzel für den Länderfilter und Flaggen-Emojis.

Die Liste entspricht /api/v2/countries (Stand 15.10.2026, 27 Märkte). Achtung: Vinted
schreibt Großbritannien teils "UK" statt ISO "GB" – wir akzeptieren beides.
"""

from __future__ import annotations

KNOWN_COUNTRIES: frozenset[str] = frozenset(
  {
    "AT", "AU", "BE", "CZ", "DE", "DK", "EE", "ES", "FI", "FR", "GB", "GR", "HR", "HU",
    "IE", "IT", "LT", "LU", "LV", "NL", "PL", "PT", "RO", "SE", "SI", "SK", "US",
  }
)  # fmt: skip

_ALIASES = {"UK": "GB"}

UNKNOWN_FLAG = "❔"


class InvalidCountryCodeError(ValueError):
  pass


def normalize_country(code: str | None) -> str | None:
  """'de' -> 'DE', 'uk' -> 'GB', leer/None -> None. Unbekannte Kürzel bleiben erhalten."""
  if not code:
    return None
  code = code.strip().upper()
  return _ALIASES.get(code, code) or None


def parse_country_codes(text: str | None) -> tuple[str, ...]:
  """'at, de,IT' -> ('AT', 'DE', 'IT'). Leer -> () bedeutet: alle Länder.

  Wirft InvalidCountryCodeError mit allen unbekannten Kürzeln auf einmal.
  """
  if not text or not text.strip():
    return ()
  codes: dict[str, None] = {}  # geordnete Menge
  unknown: list[str] = []
  for part in text.replace(";", ",").replace(" ", ",").split(","):
    code = normalize_country(part)
    if code is None:
      continue
    if code not in KNOWN_COUNTRIES:
      unknown.append(part.strip())
      continue
    codes[code] = None
  if unknown:
    raise InvalidCountryCodeError(
      f"Unbekannte Länderkürzel: {', '.join(unknown)}. "
      f"Erlaubt: {', '.join(sorted(KNOWN_COUNTRIES)).lower()}"
    )
  return tuple(codes)


def flag_emoji(code: str | None) -> str:
  """'DE' -> 🇩🇪. Flaggen sind zwei 'Regional Indicator'-Zeichen (A = U+1F1E6)."""
  code = normalize_country(code)
  if code is None or len(code) != 2 or not code.isalpha():
    return UNKNOWN_FLAG
  return "".join(chr(0x1F1E6 + ord(letter) - ord("A")) for letter in code)
