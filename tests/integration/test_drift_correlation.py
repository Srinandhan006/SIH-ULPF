"""Empirical validation of Hypothesis H2 (docs/problem-statement.md #9: 'correlated with parser
drift'; uli/drift/correlator.py's docstring calls it out by name). Previously this was purely a
documented hypothesis with no test exercising it end-to-end through the real pipeline -- this
drives an actual resource-drift event and an actual parser-drift event (via structurally different
JSON under the same known parser) through Pipeline._compute and asserts the correlator actually
finds the resource-drift event as an explanation, not just that the correlator function returns
*something* in isolation."""
from __future__ import annotations

import yaml

from uli.drift.resource import ResourceDriftAgent
from uli.models import RawEnvelope

SHAPE_A = [
    '{"level":"info","msg":"request completed","host":"h1","status":200}',
    '{"level":"info","msg":"request completed","host":"h2","status":200}',
]
SHAPE_B = [
    '{"level":"warn","event":"disk_pressure","node":"n1","pct_used":92,"threshold":90,"mount":"/data"}',
    '{"level":"warn","event":"disk_pressure","node":"n2","pct_used":95,"threshold":90,"mount":"/data"}',
]


def _feed(stack, lines, source_id, count):
    for i in range(count):
        env = RawEnvelope.from_bytes(f"{lines[i % len(lines)]}".encode(), tenant_id="default", source_id=source_id, transport="http")
        stack.pipeline.process(env)


def test_resource_drift_explains_concurrent_parser_drift(stack, tmp_path):
    # tmp_settings fixture (tests/conftest.py) sets drift_window_events=5, drift_window_seconds=2,
    # drift_consecutive_windows defaults to 2 -- so 2 windows of shape A (reference + confirm), then
    # 2 windows of shape B (1st hit, 2nd confirms) trigger a ParserDriftEvent.
    source_id = "corr-test:1"
    _feed(stack, SHAPE_A, source_id, 5)  # window 1: sets reference
    _feed(stack, SHAPE_A, source_id, 5)  # window 2: matches reference, no drift

    # A resource-drift event lands squarely inside the correlation window (correlation_window_s
    # defaults to 900s), simulating "the box this source runs on changed just before its parser
    # started drifting" -- the scenario H2 is actually about.
    desired_path = tmp_path / "desired.yaml"
    desired_path.write_text(yaml.safe_dump({"cpu_count": 999999}))
    stack.settings.desired_state_path = desired_path
    resource_events = ResourceDriftAgent(stack.settings, stack.storage, stack.raw_store).tick()
    assert resource_events, "test setup must actually produce a resource-drift event"
    resource_drift_id = resource_events[0].drift_id

    _feed(stack, SHAPE_B, source_id, 5)  # window 3: shape shift, 1st consecutive hit -> no event yet
    _feed(stack, SHAPE_B, source_id, 5)  # window 4: 2nd consecutive hit -> ParserDriftEvent fires

    parser_drifts = stack.storage.list_parser_drift("default")
    assert parser_drifts, "expected a parser-drift event to have fired under the shape-B windows"
    pd = parser_drifts[0]
    assert resource_drift_id in pd["explained_by"], (
        f"H2 correlation did not link resource drift {resource_drift_id} to parser drift {pd['pdrift_id']}: "
        f"explained_by={pd['explained_by']}"
    )


def test_correlation_does_not_falsely_explain_drift_outside_window(stack, tmp_path):
    """Negative control: a resource-drift event correlates only when it precedes the parser-drift
    event within the configured window -- with a zero-width window it must never appear."""
    stack.settings.correlation_window_s = 0.0
    source_id = "corr-test:2"
    _feed(stack, SHAPE_A, source_id, 5)
    _feed(stack, SHAPE_A, source_id, 5)

    desired_path = tmp_path / "desired.yaml"
    desired_path.write_text(yaml.safe_dump({"cpu_count": 999999}))
    stack.settings.desired_state_path = desired_path
    resource_events = ResourceDriftAgent(stack.settings, stack.storage, stack.raw_store).tick()
    resource_drift_id = resource_events[0].drift_id

    _feed(stack, SHAPE_B, source_id, 5)
    _feed(stack, SHAPE_B, source_id, 5)

    parser_drifts = stack.storage.list_parser_drift("default")
    assert parser_drifts
    assert resource_drift_id not in parser_drifts[0]["explained_by"]
