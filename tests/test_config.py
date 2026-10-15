from pathlib import Path

import pytest

from vinted_bot.config import HONEST_USER_AGENT, ConfigError, parse_settings

VALID = {"DISCORD_TOKEN": "secret-token", "GUILD_ID": "123", "OWNER_ID": "456"}


def test_minimal_config_uses_defaults() -> None:
  settings = parse_settings(VALID)
  assert settings.guild_id == 123
  assert settings.owner_id == 456
  assert settings.admin_channel_id is None
  assert settings.default_interval_s == 60
  assert settings.global_min_spacing_s == 5.0
  assert settings.vinted_user_agent == HONEST_USER_AGENT
  assert settings.db_path == Path("data/bot.db")
  assert settings.log_level == "INFO"


def test_token_never_appears_in_repr() -> None:
  assert "secret-token" not in repr(parse_settings(VALID))


def test_all_problems_are_reported_at_once() -> None:
  with pytest.raises(ConfigError) as info:
    parse_settings({"GUILD_ID": "abc", "DEFAULT_INTERVAL_S": "10", "LOG_LEVEL": "loud"})
  problems = "\n".join(info.value.problems)
  assert "DISCORD_TOKEN fehlt" in problems
  assert "GUILD_ID muss eine ganze Zahl sein" in problems
  assert "OWNER_ID fehlt" in problems
  assert "DEFAULT_INTERVAL_S muss >= 30" in problems
  assert "LOG_LEVEL" in problems


def test_optional_values_are_parsed() -> None:
  settings = parse_settings(
    {
      **VALID,
      "ADMIN_CHANNEL_ID": "789",
      "VINTED_USER_AGENT": "Custom/1.0",
      "GLOBAL_MIN_SPACING_S": "7.5",
      "LOG_LEVEL": "debug",
    }
  )
  assert settings.admin_channel_id == 789
  assert settings.vinted_user_agent == "Custom/1.0"
  assert settings.global_min_spacing_s == 7.5
  assert settings.log_level == "DEBUG"


def test_whitespace_only_values_count_as_missing() -> None:
  with pytest.raises(ConfigError):
    parse_settings({**VALID, "DISCORD_TOKEN": "   "})
