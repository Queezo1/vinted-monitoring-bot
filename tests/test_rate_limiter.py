import asyncio

from vinted_bot.vinted.rate_limiter import GlobalRateLimiter


class FakeTime:
  """Uhr und sleep in einem: sleep spult die Uhr nur vor, ohne echt zu warten."""

  def __init__(self) -> None:
    self.now = 100.0
    self.sleeps: list[float] = []

  def clock(self) -> float:
    return self.now

  async def sleep(self, seconds: float) -> None:
    self.sleeps.append(seconds)
    self.now += seconds
    await asyncio.sleep(0)  # wie ein echtes sleep: andere Tasks kommen dran


async def test_first_request_is_immediate_then_spacing_applies() -> None:
  fake = FakeTime()
  limiter = GlobalRateLimiter(5.0, clock=fake.clock, sleep=fake.sleep)
  await limiter.acquire()
  assert fake.sleeps == []
  fake.now += 2.0
  await limiter.acquire()
  assert fake.sleeps == [3.0]


async def test_no_wait_if_enough_time_passed() -> None:
  fake = FakeTime()
  limiter = GlobalRateLimiter(5.0, clock=fake.clock, sleep=fake.sleep)
  await limiter.acquire()
  fake.now += 10
  await limiter.acquire()
  assert fake.sleeps == []


async def test_concurrent_callers_are_serialised() -> None:
  fake = FakeTime()
  limiter = GlobalRateLimiter(5.0, clock=fake.clock, sleep=fake.sleep)
  await asyncio.gather(*(limiter.acquire() for _ in range(4)))
  assert fake.sleeps == [5.0, 5.0, 5.0]


async def test_low_priority_waits_for_high_priority() -> None:
  fake = FakeTime()
  limiter = GlobalRateLimiter(5.0, clock=fake.clock, sleep=fake.sleep)
  order: list[str] = []

  async def request(name: str, low: bool) -> None:
    await limiter.acquire(low_priority=low)
    order.append(name)

  await limiter.acquire()  # belegt den ersten Slot
  # Nachladen (niedrig) wird vor dem Katalog (hoch) angefordert, kommt aber danach dran
  await asyncio.gather(request("hoch-1", False), request("niedrig", True), request("hoch-2", False))
  assert order == ["hoch-1", "hoch-2", "niedrig"]


async def test_penalize_slows_down_and_relaxes_after_calm_period() -> None:
  fake = FakeTime()
  limiter = GlobalRateLimiter(3.0, clock=fake.clock, sleep=fake.sleep)
  limiter.penalize()
  assert limiter.spacing_s == 4.5
  for _ in range(5):
    limiter.penalize()
  assert limiter.spacing_s == 10.0  # Obergrenze
  fake.now += 30 * 60
  await limiter.acquire()
  assert limiter.spacing_s == 10.0 / 1.5  # eine Stufe schneller nach 30 ruhigen Minuten
  for _ in range(10):
    fake.now += 30 * 60
    await limiter.acquire()
  assert limiter.spacing_s == 3.0  # nie schneller als der Ausgangswert
