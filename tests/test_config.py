import pytest

from festpass.config import ConfigError, load_lineup
from tests.conftest import LINEUP


def write(tmp_path, text):
    path = tmp_path / "lineup.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_acts_are_sorted_by_day(tmp_path):
    lineup = load_lineup(write(tmp_path, LINEUP))
    assert [a.partner for a in lineup.acts] == ["Зерно", "Вжух", "Книжная лавка"]
    assert lineup.acts[0].prize_at(3) == "стикеры"


def test_promo_cannot_expire_before_its_day(tmp_path):
    broken = LINEUP.replace("promo_until = 2026-09-30", "promo_until = 2026-08-30")
    with pytest.raises(ConfigError, match="истекает"):
        load_lineup(write(tmp_path, broken))


def test_two_partners_on_one_day(tmp_path):
    broken = LINEUP.replace("day = 2026-09-02", "day = 2026-09-01")
    with pytest.raises(ConfigError, match="один партнер"):
        load_lineup(write(tmp_path, broken))
