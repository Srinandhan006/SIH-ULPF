import yaml

from uli.drift.resource import ResourceDriftAgent, diff, observe_system


def test_observe_system_returns_sane_values():
    obs = observe_system()
    assert obs["cpu_count"] > 0
    assert obs["memory_gb"] > 0


def test_diff_detects_cpu_mismatch():
    desired = {"cpu_count": 999}
    observed = {"cpu_count": 4}
    events = diff(desired, observed)
    assert any(e.kind == "cpu" for e in events)


def test_diff_no_events_when_matching():
    observed = observe_system()
    desired = {"cpu_count": observed["cpu_count"]}
    events = diff(desired, observed)
    assert not any(e.kind == "cpu" for e in events)


def test_resource_drift_agent_writes_events_with_provenance(stack, tmp_path):
    desired_path = tmp_path / "desired.yaml"
    desired_path.write_text(yaml.safe_dump({"cpu_count": 999999}))
    stack.settings.desired_state_path = desired_path
    agent = ResourceDriftAgent(stack.settings, stack.storage, stack.raw_store)
    events = agent.tick()
    assert events
    for e in events:
        found = stack.storage.get_raw_location("default", e.snapshot_raw_event_id)
        assert found is not None, "resource drift snapshot must be traceable to raw store like any other event"
    stored = stack.storage.list_drift("default")
    assert stored
