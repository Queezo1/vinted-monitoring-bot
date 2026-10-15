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
