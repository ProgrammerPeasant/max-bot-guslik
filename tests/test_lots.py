from collections import Counter

from festpass import lots


def test_same_seed_same_result():
    weights = {uid: 1 for uid in range(100)}
    assert lots.draw("abc", weights, 5) == lots.draw("abc", weights, 5)
    assert lots.draw("abc", weights, 5) != lots.draw("abd", weights, 5)


def test_nobody_wins_twice():
    weights = {1: 10, 2: 1, 3: 1}
    assert sorted(lots.draw("seed", weights, 10)) == [1, 2, 3]


def test_zero_weight_never_wins():
    assert lots.draw("seed", {1: 0, 2: 1}, 2) == [2]


def test_weight_works_like_extra_tickets():
    # у первого три билета, у второго один: первый должен побеждать примерно в 3/4 розыгрышей
    wins = Counter(lots.draw(f"seed-{n}", {1: 3, 2: 1}, 1)[0] for n in range(4000))
    assert 0.72 < wins[1] / 4000 < 0.78


def test_fingerprint_is_sha256_of_seed():
    assert lots.fingerprint("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
