"""The flame curve."""

from custom_components.proflame2_broadlink.thermostat import Curve


def run(curve, shortfalls, smart, start=0):
    flame, out = start, []
    for shortfall in shortfalls:
        flame = curve.flame(shortfall, flame, smart)
        out.append(flame)
    return out


def test_plain_heat_is_on_off_with_margin():
    curve = Curve(hysteresis=0.5)
    #                    idle  idle  start  hold  hold  hold   stop
    assert run(curve, [-1.0, 0.4, 0.6, 0.1, 0.0, -0.4, -0.6], smart=False) == [
        0, 0, 6, 6, 6, 6, 0,
    ]


def test_smart_default_is_one_level_per_degree():
    curve = Curve(hysteresis=0)
    assert run(curve, [0.2, 1.0, 1.1, 2.5, 5.9, 6.0, 9.0, 0.0], smart=True) == [
        1, 1, 2, 3, 6, 6, 6, 0,
    ]


def test_smart_does_not_hunt_on_a_threshold():
    curve = Curve(hysteresis=0.5)
    # Room wobbling around the 2 F threshold between levels 2 and 3.
    assert run(curve, [2.2, 1.9, 2.1, 1.8, 2.3], smart=True) == [3, 3, 3, 3, 3]
    # A real move still gets through.
    assert run(curve, [2.2, 1.4], smart=True) == [3, 2]


def test_smart_curve_is_configurable():
    curve = Curve(span=3.0, min_flame=2, hysteresis=0)
    assert run(curve, [0.1, 0.9, 1.1, 2.0, 3.0, 5.0], smart=True) == [2, 2, 3, 4, 6, 6]
