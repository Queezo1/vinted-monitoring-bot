"""URL-Bausteine für Vinted. Eine Stelle, falls Vinted Pfade wieder ändert.

Hinweis: Einen Kauf-Deeplink gibt es nicht mehr (alter Pfad /transaction/buy/new liefert 404,
Stand 15.10.2026). Gekauft wird auf der Artikelseite – der Bot kauft nie selbst.
"""

from __future__ import annotations

# Seit ca. 15.09.2026 liegt die Katalogsuche auf dem api.-Host (vorher /api/v2/catalog/items).
CATALOG_PATH = "/svc-catalogue/items"


def site_root(domain: str) -> str:
  """Startseite, z. B. https://www.vinted.at/ – liefert das anonyme Session-Cookie."""
  return f"https://www.vinted.{domain}/"


def api_base(domain: str) -> str:
  return f"https://api.vinted.{domain}"


def catalog_url(domain: str) -> str:
  return api_base(domain) + CATALOG_PATH


def user_url(domain: str, user_id: int) -> str:
  """Öffentliches Verkäuferprofil als JSON (Land, Bewertung, Avatar). Liegt auf dem www-Host."""
  return f"https://www.vinted.{domain}/api/v2/users/{user_id}"


def item_url(domain: str, item_id: int) -> str:
  return f"https://www.vinted.{domain}/items/{item_id}"


def member_url(domain: str, user_id: int) -> str:
  return f"https://www.vinted.{domain}/member/{user_id}"
