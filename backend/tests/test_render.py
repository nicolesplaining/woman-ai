"""The review renders must show what's actually in front, or Claude judges the fit from a wrong picture."""
import numpy as np
import trimesh

from app.render import Layer, render_ortho


def _square(z: float) -> trimesh.Trimesh:
    # A 20 x 20 mm square in the x-y plane at height z, facing +z (toward a camera looking down -z).
    v = np.array([[-10, -10, z], [10, -10, z], [10, 10, z], [-10, 10, z]], float)
    return trimesh.Trimesh(v, [[0, 1, 2], [0, 2, 3]], process=False)


def test_nearer_layer_wins_whatever_the_layer_order():
    near = Layer(_square(5.0), (255, 0, 0))
    far = Layer(_square(-5.0), (0, 0, 255))
    light = np.array([0.0, 0.0, 1.0])  # full brightness on +z faces

    img, _ = render_ortho([near, far], (0, 0, -1), (0, 1, 0), size=(100, 100), px_per_mm=2.0, light=light)

    r, g, b = img[50, 50]
    assert r > 200 and b < 50
