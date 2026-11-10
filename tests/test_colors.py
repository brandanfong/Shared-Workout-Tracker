import itertools

import pytest

import colors


def test_presets_all_pass_the_rules_and_are_distinct():
    for c in colors.PRESETS:
        assert colors.check(c) is None, c
    for a, b in itertools.combinations(colors.PRESETS, 2):
        assert colors.distance(a, b) >= colors.MIN_DISTANCE, (a, b)


def test_default_palette_passes_the_rules():
    for c in colors.DEFAULT_PALETTE:
        assert colors.check(c) is None, c


@pytest.mark.parametrize("bad", ["", "red", "#fff", "3b82f6", "#3b82f", "#gggggg", None, 5])
def test_normalize_rejects_non_hex(bad):
    assert colors.normalize(bad) is None


def test_normalize_lowercases():
    assert colors.normalize("#3B82F6") == "#3b82f6"


@pytest.mark.parametrize("gray", ["#808080", "#ffffff", "#000000", "#777788"])
def test_grays_rejected(gray):
    assert "gray" in colors.check(gray)


def test_too_dark_and_too_pale_rejected():
    assert "dark" in colors.check("#200040")
    assert "pale" in colors.check("#ffd0d0")


def test_too_close_to_another_person_rejected():
    assert "Sam" in colors.check("#3c83f6", {"sam": "#3b82f6"})
    assert colors.check("#f97316", {"sam": "#3b82f6"}) is None


def test_everyone_color_is_reserved():
    assert colors.check(colors.EVERYONE_COLOR) is not None
    assert "everyone" in colors.check("#17a34b")  # a near match is rejected too
    for c in colors.PRESETS + colors.DEFAULT_PALETTE:
        assert colors.distance(c, colors.EVERYONE_COLOR) >= colors.MIN_DISTANCE, c
