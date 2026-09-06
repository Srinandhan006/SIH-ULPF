from uli.fingerprint import fingerprint
from uli.models import ParseContext
from uli.parsers.structural import CEFParser, CLFParser, JSONParser, KVParser, LEEFParser, Syslog3164Parser, Syslog5424Parser


def ctx(text: str) -> ParseContext:
    return ParseContext(raw=text.encode(), text=text, fingerprint=fingerprint(text), tenant_id="t", source_id="s", transport="test", hints={})


def test_json_parser_extracts_timestamp_and_message():
    p = JSONParser()
    c = ctx('{"timestamp":"2026-09-05T10:00:00Z","level":"error","msg":"disk full","host":"node1"}')
    assert p.can_parse(c) > 0.5
    ir = p.parse(c)
    assert ir.timestamp is not None and ir.message == "disk full" and ir.severity == 4


def test_json_parser_invalid_json_does_not_raise():
    p = JSONParser()
    c = ctx('{"a": }')
    ir = p.parse(c)
    assert ir.confidence == 0.0 and ir.errors


def test_cef_parser():
    p = CEFParser()
    line = "CEF:0|Netgate|pfSense|2.7|1000000103|Default deny rule|5|src=10.0.0.7 dst=93.184.216.34 spt=51234 dpt=443 proto=TCP act=block"
    c = ctx(line)
    assert p.can_parse(c) > 0.9
    ir = p.parse(c)
    assert ir.vendor == "Netgate" and ir.fields["src"] == "10.0.0.7" and ir.fields["dpt"] == "443"


def test_cef_parser_escaped_pipes():
    p = CEFParser()
    line = r"CEF:0|Acme|Box\|Pro|1.0|100|Test\|Name|3|msg=hello\=world"
    ir = p.parse(ctx(line))
    assert ir.product == "Box|Pro" and ir.fields["msg"] == "hello=world"


def test_leef_parser():
    p = LEEFParser()
    line = "LEEF:2.0|Acme|Firewall|1.0|Deny|src=1.2.3.4\tdst=5.6.7.8\tdevTime=2026-09-05T10:00:00Z"
    ir = p.parse(ctx(line))
    assert ir.vendor == "Acme" and ir.fields.get("src") == "1.2.3.4"


def test_syslog5424():
    p = Syslog5424Parser()
    line = "<34>1 2026-09-05T10:00:00.123Z host01 sshd 1234 - - Failed password for invalid user admin from 10.0.0.7 port 51234 ssh2"
    c = ctx(line)
    assert p.can_parse(c) > 0.9
    ir = p.parse(c)
    assert ir.fields["syslog.appname"] == "sshd" and ir.timestamp is not None


def test_syslog3164_bsd():
    p = Syslog3164Parser()
    line = "Sep  5 10:00:01 host01 sshd[1234]: Accepted publickey for root from 192.168.1.5 port 22 ssh2"
    c = ctx(line)
    assert p.can_parse(c) > 0.7
    ir = p.parse(c)
    assert ir.fields["syslog.appname"] == "sshd" and ir.fields["syslog.procid"] == "1234"


def test_clf():
    p = CLFParser()
    line = '10.0.0.1 - - [05/Sep/2026:10:00:00 +0000] "GET /index.html HTTP/1.1" 200 1234'
    ir = p.parse(ctx(line))
    assert ir.fields["http.status"] == 200 and ir.class_uid == 4002


def test_kv_parser():
    p = KVParser()
    line = 'level=error msg="disk full" host=node1 code=42'
    ir = p.parse(ctx(line))
    assert ir.fields["msg"] == "disk full" and ir.severity == 4


def test_all_structural_parsers_are_total_on_garbage():
    """No structural parser may raise on adversarial/garbage input (P1)."""
    garbage = ["", "\x00\x01\x02", "{" * 10000, "=" * 5000, "CEF:", "LEEF:", "<999999999>1 x", "a" * 200000]
    from uli.parsers.structural import STRUCTURAL_PARSERS

    for g in garbage:
        c = ctx(g[:70000])
        for parser in STRUCTURAL_PARSERS:
            score = parser.can_parse(c)
            assert 0.0 <= score <= 1.0
            if score > 0:
                ir = parser.parse(c)  # must not raise
                assert ir is not None
