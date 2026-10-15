import os
from pathlib import Path

import pytest

from vinted_bot.single_instance import AlreadyRunningError, InstanceLock


def test_second_instance_is_rejected_and_pid_stays_readable(tmp_path: Path) -> None:
  path = tmp_path / "data" / "bot.lock"
  with InstanceLock(path):
    assert path.read_text() == str(os.getpid())  # für stop_bot.ps1 lesbar
    with pytest.raises(AlreadyRunningError):
      InstanceLock(path).acquire()
    assert path.read_text() == str(os.getpid())  # vom Fehlversuch nicht überschrieben


def test_lock_is_free_again_after_release(tmp_path: Path) -> None:
  path = tmp_path / "bot.lock"
  InstanceLock(path).__enter__().release()
  with InstanceLock(path):
    pass
