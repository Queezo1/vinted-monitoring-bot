import pytest

from vinted_bot.core.filter_service import FilterService, UserError
from vinted_bot.storage.repository import Repository

LINK = "https://www.vinted.at/catalog?catalog[]=79&brand_ids[]=88&price_to=40&time=1"
JACKETS = "https://www.vinted.at/catalog?catalog[]=1206&brand_ids[]=88"


class FakeManager:
  """Merkt sich nur, welche Poller gestartet/gestoppt wurden."""

  def __init__(self) -> None:
    self.running: set[int] = set()

  def start(self, filter_id: int, *, delay_s: float = 0.0) -> None:
    self.running.add(filter_id)

  async def stop(self, filter_id: int) -> None:
    self.running.discard(filter_id)


@pytest.fixture
def manager() -> FakeManager:
  return FakeManager()


@pytest.fixture
def service(repo: Repository, manager: FakeManager) -> FilterService:
  return FilterService(repo, manager, default_interval_s=60)  # type: ignore[arg-type]


async def test_create_and_start(service: FilterService, manager: FakeManager) -> None:
  prepared = service.prepare(LINK, "at, de", None)
  flt = await service.create(prepared, "rl-pullover", channel_id=10)
  assert flt.name == "rl-pullover"
  assert flt.countries == ("AT", "DE")
  assert flt.interval_s == 60
  assert flt.api_params["attribute_ids[brand]"] == "88"
  assert manager.running == {flt.id}


async def test_several_filters_run_in_parallel(
  service: FilterService, manager: FakeManager
) -> None:
  a = await service.create(service.prepare(LINK, None, None), "pullover", 10)
  b = await service.create(service.prepare(JACKETS, None, None), "jacken", 11)
  same_link_other_country = await service.create(service.prepare(LINK, "it", None), "it", 10)
  assert manager.running == {a.id, b.id, same_link_other_country.id}


async def test_duplicate_in_same_channel_is_rejected(service: FilterService) -> None:
  await service.create(service.prepare(LINK, None, None), "a", 10)
  # gleicher Filter, nur andere Tracking-Parameter -> trotzdem Duplikat
  prepared = service.prepare(LINK.replace("time=1", "time=2"), None, None)
  assert await service.is_duplicate(prepared, 10)
  with pytest.raises(UserError, match="schon"):
    await service.create(prepared, "b", 10)


@pytest.mark.parametrize(
  ("link", "countries", "interval", "message"),
  [
    ("https://example.com", None, None, "Ungültiger Link"),
    (LINK, "at,xx", None, "Unbekannte Länderkürzel"),
    (LINK, None, 5, "Intervall"),
    (LINK, None, 99999, "Intervall"),
  ],
)
def test_invalid_input(
  service: FilterService, link: str, countries: str | None, interval: int | None, message: str
) -> None:
  with pytest.raises(UserError, match=message):
    service.prepare(link, countries, interval)


async def test_pause_resume(service: FilterService, manager: FakeManager) -> None:
  flt = await service.create(service.prepare(LINK, None, None), "a", 10)
  assert (await service.pause(flt.id)).paused
  assert manager.running == set()
  with pytest.raises(UserError, match="bereits pausiert"):
    await service.pause(flt.id)
  assert not (await service.resume(flt.id)).paused
  assert manager.running == {flt.id}


async def test_remove_reports_unused_channel(service: FilterService, manager: FakeManager) -> None:
  a = await service.create(service.prepare(LINK, None, None), "a", 10)
  b = await service.create(service.prepare(JACKETS, None, None), "b", 10)
  _, unused = await service.remove(a.id)
  assert unused is False  # b postet noch in Kanal 10
  _, unused = await service.remove(b.id)
  assert unused is True
  assert manager.running == set()
  assert await service.list() == []


async def test_change_countries_and_interval(service: FilterService) -> None:
  flt = await service.create(service.prepare(LINK, "at", None), "a", 10)
  assert (await service.set_countries(flt.id, "de,it")).countries == ("DE", "IT")
  assert (await service.set_countries(flt.id, "")).countries == ()
  assert (await service.set_interval(flt.id, 120)).interval_s == 120


async def test_unknown_filter_id(service: FilterService) -> None:
  with pytest.raises(UserError, match="#42"):
    await service.pause(42)
