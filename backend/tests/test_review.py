"""Claude's render review may only nudge four bounded settings, for at most three passes."""
from dataclasses import replace

import pytest

from app.gauntlet import GauntletParams
from app.review import MAX_PASSES, apply_adjustments, review_bounds, review_loop

BOUNDS = review_bounds(gap=(2.0, 6.0), length=(60.0, 150.0))


def params(**kw):
    base = GauntletParams(width_scale=1.2, length_scale=1.1, gap_mm=4.0, length_mm=90.0)
    return replace(base, **kw)


def test_adjustments_are_clamped_to_bounds():
    new, changes = apply_adjustments(params(), {"gap_mm": 12.0, "opening_deg": 20.0}, BOUNDS)

    assert new.gap_mm == 6.0
    assert new.opening_deg == BOUNDS["opening_deg"][0]
    assert len(changes) == 2


def test_unknown_or_empty_adjustments_change_nothing():
    p = params()
    new, changes = apply_adjustments(p, {"wall_mm": 1.0, "gap_mm": None, "gauntlet_length_mm": -1}, BOUNDS)

    assert new == p
    assert changes == []


def test_strap_positions_are_bounded_and_ordered():
    new, _ = apply_adjustments(params(), {"strap_near": 0.05, "strap_far": 0.99}, BOUNDS)

    near, far = new.strap_positions
    assert BOUNDS["strap_near"][0] <= near <= BOUNDS["strap_near"][1]
    assert BOUNDS["strap_far"][0] <= far <= BOUNDS["strap_far"][1]
    assert near < far


class Recorder:
    """Stands in for the gauntlet builder, the renderer and Claude."""

    def __init__(self, verdicts):
        self.verdicts = list(verdicts)
        self.built, self.asked = [], 0

    def build(self, p):
        self.built.append(p)
        return {"params": p}

    def render(self, result):
        return [b"jpeg"]

    def ask(self, images, settings, bounds):
        self.asked += 1
        return self.verdicts.pop(0) if self.verdicts else {"looks_good": False, "adjustments": {"gap_mm": settings["gap_mm"] + 0.5}}


def test_stops_as_soon_as_the_fit_looks_good():
    r = Recorder([{"looks_good": True, "adjustments": {}}])

    _, final, history = review_loop(params(), r.build, r.render, r.ask, BOUNDS, first_result={"params": params()})

    assert r.asked == 1
    assert r.built == []
    assert final == params()
    assert len(history) == 1


def test_never_runs_more_than_three_passes():
    r = Recorder([])

    _, final, history = review_loop(params(), r.build, r.render, r.ask, BOUNDS, first_result={"params": params()})

    assert MAX_PASSES == 3
    assert r.asked == 3
    assert len(history) == 3
    assert final.gap_mm == pytest.approx(5.5)


def test_stops_when_the_requested_change_is_out_of_bounds_already():
    r = Recorder([{"looks_good": False, "adjustments": {"gap_mm": 9.0}}, {"looks_good": False, "adjustments": {"gap_mm": 9.0}}])

    _, final, _ = review_loop(params(gap_mm=6.0), r.build, r.render, r.ask, BOUNDS, first_result={"params": params(gap_mm=6.0)})

    assert r.asked == 1
    assert r.built == []
    assert final.gap_mm == 6.0
