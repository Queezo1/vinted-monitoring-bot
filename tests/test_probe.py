"""Tests für die reinen Hilfsfunktionen der M0-Probe (kein Netzwerk)."""

import httpx

from scripts.probe import (
  anonymize_catalog,
  describe_block_signals,
  field_presence,
  flatten_keys,
  get_path,
  main,
)

SAMPLE = {
  "items": [
    {
      "id": 1,
      "title": "Jacke",
      "price": {"amount": "10.0", "currency_code": "EUR"},
      "photo": {
        "url": "https://images1.vinted.net/real.jpg",
        "thumbnails": [{"url": "https://images1.vinted.net/t.jpg"}],
        "high_resolution": {"timestamp": 1760000000},
      },
      "user": {
        "id": 555,
        "login": "echte_person",
        "profile_url": "https://www.vinted.at/member/555",
      },
      "search_tracking_params": {"score": 1.0},
    },
    {"id": 2, "title": "Hose", "user": {"id": 555, "login": "echte_person"}, "brand_title": ""},
  ]
}


def test_get_path() -> None:
  item = SAMPLE["items"][0]
  assert get_path(item, "price.amount") == "10.0"
  assert get_path(item, "photo.high_resolution.timestamp") == 1760000000
  assert get_path(item, "user.feedback_reputation") is None
  assert get_path(item, "title.amount") is None  # kein dict unterwegs


def test_field_presence_treats_empty_values_as_missing() -> None:
  counts = field_presence(SAMPLE["items"], ["title", "brand_title", "price.amount"])
  assert counts == {"title": 2, "brand_title": 0, "price.amount": 1}


def test_flatten_keys() -> None:
  keys = flatten_keys({"a": {"b": 1}, "c": [{"d": 2}]})
  assert keys == ["a", "a.b", "c", "c[0].d"]


def test_anonymize_removes_personal_data_and_keeps_structure() -> None:
  result = anonymize_catalog(SAMPLE)
  dumped = str(result)
  assert "echte_person" not in dumped
  assert "555" not in dumped
  assert "vinted.net" not in dumped
  assert "search_tracking_params" not in dumped
  # gleicher Verkäufer -> gleicher Platzhalter
  assert result["items"][0]["user"]["login"] == result["items"][1]["user"]["login"] == "seller_1"
  # Struktur und Nicht-Personendaten bleiben
  assert result["items"][0]["photo"]["high_resolution"]["timestamp"] == 1760000000
  assert result["items"][0]["title"] == "Jacke"
  # Original unverändert
  assert SAMPLE["items"][0]["user"]["login"] == "echte_person"


def test_block_signals() -> None:
  response = httpx.Response(
    403,
    headers={"cf-ray": "abc", "cf-mitigated": "challenge", "set-cookie": "datadome=x; Path=/"},
    text="<html>Please complete the captcha</html>",
  )
  signals = describe_block_signals(response)
  assert any("cf-ray" in s for s in signals)
  assert any("Challenge" in s for s in signals)
  assert "DataDome" in signals
  assert any("Captcha" in s for s in signals)


def test_dry_run_makes_no_network_calls(capsys) -> None:
  # respx ist nicht aktiv: jeder echte Request würde hier fehlschlagen bzw. hängen
  exit_code = main(["https://www.vinted.at/catalog?search_text=nike&brand_ids[]=53", "--dry-run"])
  out = capsys.readouterr().out
  assert exit_code == 0
  assert "attribute_ids[brand] = 53" in out
  assert "api.vinted.at/svc-catalogue/items" in out


def test_invalid_url_exit_code() -> None:
  assert main(["https://example.com", "--dry-run"]) == 2
