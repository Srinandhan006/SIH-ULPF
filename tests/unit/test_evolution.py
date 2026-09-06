"""Validation of hypothesis H1 (docs/research.md §9.3): label-transfer parser evolution."""
from uli.drift.evolution import align, synthesize_pack_yaml, transfer_labels


def test_alignment_identical_sequences():
    toks = ["Failed", "password", "for", "admin", "from", "10.0.0.7", "port", "51234"]
    a = align(toks, toks)
    assert all(p.a_index == p.b_index for p in a)


def test_alignment_handles_inserted_field():
    old = ["Failed", "password", "for", "admin", "from", "10.0.0.7", "port", "51234"]
    new = ["Failed", "password", "for", "admin", "from", "10.0.0.7", "port", "51234", "proto", "ssh2"]  # v2 adds trailing fields
    a = align(old, new)
    matched = [p for p in a if p.a_index is not None and p.b_index is not None]
    assert len(matched) >= len(old) - 1  # nearly everything should still line up


def test_label_transfer_survives_field_reorder_and_addition():
    """The exact scenario from the problem statement: OLD 'timestamp host process[pid]: message'
    NEW 'timestamp host process(pid): severity message' — a field is added mid-sequence."""
    old_tokens = ["host01", "sshd", "[", "1234", "]", ":", "Accepted", "for", "root", "from", "10.0.0.7"]
    old_labels = {0: "host", 3: "pid", 10: "src_ip"}
    new_tokens = ["host01", "sshd", "(", "1234", ")", ":", "INFO", "Accepted", "for", "root", "from", "10.0.0.7"]
    new_positions, score = transfer_labels(old_tokens, old_labels, new_tokens)
    assert new_positions.get(0) == "host"
    assert new_positions.get(3) == "pid"
    assert new_positions.get(11) == "src_ip"
    assert score >= 2 / 3  # host + pid + src_ip carried, at least 2 of 3


def test_synthesize_pack_yaml_produces_loadable_pack():
    import yaml as _yaml
    from pathlib import Path
    from uli.parsers.declarative import validate_pack

    base_spec = {"parser_id": "vendor.test.v1", "version": "1.0.0", "signatures": [{"kind": "prefix", "startswith": "x", "required": True}], "map": {"fields": {}}}
    positions = {0: "host", 3: "pid"}
    yaml_text = synthesize_pack_yaml(base_spec, positions, ["a", "b", "c", "d"], score=0.8)
    spec = _yaml.safe_load(yaml_text)
    assert spec["version"] == "1.1.0"
    errs = validate_pack(spec, Path("schemas"))
    assert not errs
