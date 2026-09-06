from pathlib import Path

import pytest

from uli.fingerprint import fingerprint
from uli.models import ParseContext
from uli.parsers.declarative import load_pack_yaml, validate_pack
import yaml

SCHEMAS = Path("schemas")


def ctx(text: str, hints=None) -> ParseContext:
    return ParseContext(raw=text.encode(), text=text, fingerprint=fingerprint(text), tenant_id="t", source_id="s", transport="syslog-udp", hints=hints or {})


def test_pfsense_pack_loads_and_parses():
    text = Path("parsers/vendors/pfsense_filterlog.yaml").read_text()
    p = load_pack_yaml(text, SCHEMAS)
    line = "Sep  5 10:00:00 fw01 filterlog[123]: 1000000103,,,1000000103,igb0,match,block,in,4,0x0,,64,12345,0,DF,6,tcp,60,10.0.0.7,93.184.216.34,51234,443,0,S,123456,,64240,,mss"
    c = ctx(line)
    assert p.can_parse(c) > 0.8
    ir = p.parse(c)
    assert ir.fields.get("ocsf.src_endpoint.ip") == "10.0.0.7"
    assert ir.fields.get("ocsf.dst_endpoint.port") == 443
    assert ir.fields.get("ocsf.action_id") == 2
    assert ir.confidence > 0.5


def test_squid_pack():
    text = Path("parsers/vendors/squid_access.yaml").read_text()
    p = load_pack_yaml(text, SCHEMAS)
    line = "1425599205.123    118 10.105.21.199 TCP_MISS/200 1024 GET http://example.com/ - HIER_DIRECT/93.184.216.34 text/html"
    c = ctx(line)
    assert p.can_parse(c) > 0.5
    ir = p.parse(c)
    assert ir.fields.get("ocsf.src_endpoint.ip") == "10.105.21.199"
    assert ir.fields.get("ocsf.http_response.code") == 200


def test_zeek_conn_pack():
    text = Path("parsers/vendors/zeek_conn.yaml").read_text()
    p = load_pack_yaml(text, SCHEMAS)
    line = "1331901000.000000\tCHhAvVGS1DHFjwGM9\t192.168.202.79\t50465\t192.168.229.251\t80\ttcp\thttp\t0.5\t100\t200\tSF\tF\tF\t0\tShADadFf\t4\t272\t4\t432\t(empty)"
    c = ctx(line)
    assert p.can_parse(c) > 0.5
    ir = p.parse(c)
    assert ir.fields.get("ocsf.src_endpoint.ip") == "192.168.202.79"
    assert ir.fields.get("ocsf.dst_endpoint.port") == 80


def test_pack_schema_rejects_bad_pack():
    bad = {"parser_id": "x", "version": "not-a-semver", "signatures": []}
    errs = validate_pack(bad, SCHEMAS)
    assert errs  # minItems 1 on signatures + version pattern


def test_pack_yaml_is_safe_load_only_no_code_exec():
    malicious = "parser_id: evil.test\nversion: 1.0.0\nsignatures: [{kind: prefix, startswith: x}]\n!!python/object/apply:os.system ['echo pwned']\n"
    with pytest.raises(Exception):
        load_pack_yaml(malicious, SCHEMAS)


def test_grok_lite_expansion():
    text = "parser_id: test.grok\nversion: 1.0.0\nsignatures: [{kind: prefix, startswith: 'GROKTEST'}]\nextract:\n  - kind: grok_lite\n    field: message\n    pattern: 'GROKTEST %{IP:ip} %{INT:code}'\nmap:\n  fields:\n    src_endpoint.ip: {from: ip}\n"
    p = load_pack_yaml(text, SCHEMAS)
    c = ctx("GROKTEST 10.1.1.1 42")
    ir = p.parse(c)
    assert ir.fields.get("ocsf.src_endpoint.ip") == "10.1.1.1"
