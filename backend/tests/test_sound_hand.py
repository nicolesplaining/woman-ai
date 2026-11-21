"""The sound (other) hand sets the device size, so its breadth must match the ANSUR II definition:
width across the knuckles (metacarpal heads), not the widest point of the palm."""
import pytest

from app.photo import load_photo, measure_sound_hand, rectify, segment_arm, width_profile
from app.synthetic import SOUND, arm_model, render_photo

# The synthetic hand is 180 x 78 mm at k=1 (ANSUR II female medians); its palm is wider (80 mm)
# just behind the knuckle line, which is what a widest-point measurement picks up.
CASES = [
    (0.8, 750.0, 52.0, 3),
    (1.0, 850.0, 52.0, 5),
    (1.1, 700.0, 26.0, 7),
]


@pytest.mark.parametrize("k,height,f35,seed", CASES)
def test_sound_hand_breadth_is_knuckle_width(k, height, f35, seed):
    hand = arm_model(SOUND, k=k)
    length = 180.0 * k
    photo = render_photo(hand, "top", wrist_xy=(92.0, length + 12.0), seed=seed, height=height, f35=f35)
    rgb, lens = load_photo(photo)
    rect = rectify(rgb, lens)
    prof = width_profile(segment_arm(rect), "top")

    got = measure_sound_hand(rect, prof, reading={})

    assert got["hand_breadth"] == pytest.approx(78.0 * k, abs=1.0)
