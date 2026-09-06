from uli.drift.parser_drift import DriftMonitor, js_divergence
from collections import Counter


def test_js_divergence_zero_for_identical_distributions():
    a = Counter({"x": 10, "y": 10})
    assert js_divergence(a, a) == 0.0


def test_js_divergence_positive_for_different_distributions():
    a = Counter({"x": 100})
    b = Counter({"y": 100})
    assert js_divergence(a, b) > 0.9


def test_drift_monitor_flags_sustained_shape_shift(stack):
    dm = DriftMonitor(stack.storage, window_events=5, window_seconds=9999, js_threshold=0.1, consecutive=2, confidence_drop=0.9)
    # reference window: all shape "A"
    for _ in range(5):
        dm.observe("t", "src1", "vendor.x", "shapeA", 0.9)
    # drifted window: all shape "B" (completely different) -- needs 2 consecutive windows to confirm
    ev = None
    for _ in range(5):
        ev = dm.observe("t", "src1", "vendor.x", "shapeB", 0.9)
    assert ev is None  # first drifted window only counts as 1/2
    for _ in range(5):
        ev = dm.observe("t", "src1", "vendor.x", "shapeB", 0.9)
    assert ev is not None
    assert ev.js_divergence > 0.1


def test_drift_monitor_ignores_transient_single_window_blip(stack):
    dm = DriftMonitor(stack.storage, window_events=5, window_seconds=9999, js_threshold=0.1, consecutive=3, confidence_drop=0.9)
    for _ in range(5):
        dm.observe("t", "src1", "vendor.x", "shapeA", 0.9)
    ev = None
    for _ in range(5):
        ev = dm.observe("t", "src1", "vendor.x", "shapeB", 0.9)  # one blip window
    assert ev is None
    for _ in range(5):
        ev = dm.observe("t", "src1", "vendor.x", "shapeA", 0.9)  # reverts
    assert ev is None
