"""Onion request endpoints (v3/v4) — protocol framing contract.

session_util is stubbed to the identity function (see conftest), so POST
bodies below are the plaintext payloads the real crypto layer would emit.
"""
import base64
import json
import time

from conftest import TOKEN


def _benc(data: bytes) -> bytes:
    return str(len(data)).encode() + b":" + data


def v4_payload(meta: dict, body: bytes | None = None) -> bytes:
    parts = [json.dumps(meta).encode()] + ([body] if body is not None else [])
    return b"l" + b"".join(_benc(p) for p in parts) + b"e"


def parse_v4(reply: bytes):
    """Independent parser (do not reuse code-under-test to verify itself)."""
    assert reply[:1] == b"l" and reply[-1:] == b"e"
    buf = reply[1:-1]

    def consume(buf):
        i = 0
        while buf[i:i + 1].isdigit():
            i += 1
        assert buf[i:i + 1] == b":", "bad bencode"
        n = int(buf[:i])
        i += 1
        return buf[i:i + n], buf[i + n:]

    meta_raw, rest = consume(buf)
    data, rest = consume(rest)
    assert rest == b"", "trailing bytes"
    return json.loads(meta_raw), data


def _client(app):
    return app.test_client()


def _direct_info(client):
    body = client.get("/info").get_json()
    body.pop("t")
    return body


def _check_t(value):
    assert isinstance(value, int)
    assert abs(value - time.time()) <= 5


def test_v4_onion_info_roundtrip(info_app):
    app, _ = info_app
    client = _client(app)
    r = client.post("/oxen/v4/lsrpc", data=v4_payload(
        {"method": "GET", "endpoint": "/info"}))
    assert r.status_code == 200
    meta, data = parse_v4(r.get_data())
    assert meta["code"] == 200
    assert meta["headers"]["content-type"].startswith("application/json")
    body = json.loads(data)
    _check_t(body.pop("t"))
    assert body == _direct_info(client)


def test_v4_onion_two_part_payload_accepted(info_app):
    app, _ = info_app
    client = _client(app)
    r = client.post("/oxen/v4/lsrpc", data=v4_payload(
        {"method": "GET", "endpoint": "/prices/{}/7d".format(TOKEN)},
        body=b""))
    assert r.status_code == 200
    meta, data = parse_v4(r.get_data())
    assert meta["code"] == 200
    body = json.loads(data)
    _check_t(body.pop("t"))
    assert len(body["prices"]) == 1


def test_v4_invalid_plaintext_returns_bencoded_400(info_app):
    app, _ = info_app
    r = _client(app).post("/oxen/v4/lsrpc", data=b"this-is-not-bencoded")
    assert r.status_code == 200  # the onion layer itself succeeds
    meta, data = parse_v4(r.get_data())
    assert meta["code"] == 400
    assert data == b"Invalid v4 onion request"


def test_v4_too_many_parts_returns_400(info_app):
    app, _ = info_app
    payload = (b"l" + _benc(json.dumps(
        {"method": "GET", "endpoint": "/info"}).encode())
        + _benc(b"") + _benc(b"x") + b"e")
    r = _client(app).post("/oxen/v4/lsrpc", data=payload)
    meta, _ = parse_v4(r.get_data())
    assert meta["code"] == 400


def test_v4_endpoint_without_leading_slash_400(info_app):
    app, _ = info_app
    r = _client(app).post("/oxen/v4/lsrpc", data=v4_payload(
        {"method": "GET", "endpoint": "info"}))
    meta, _ = parse_v4(r.get_data())
    assert meta["code"] == 400


def test_v4_subrequest_error_maps_status_code(make_app):
    # /info with no price data aborts 500; the onion layer must surface it
    # in meta.code rather than failing the outer request.
    app, _ = make_app()
    r = _client(app).post("/oxen/v4/lsrpc", data=v4_payload(
        {"method": "GET", "endpoint": "/info"}))
    assert r.status_code == 200
    meta, data = parse_v4(r.get_data())
    assert meta["code"] == 500
    assert len(data) > 0


def test_v3_onion_info_roundtrip_including_android_body_null_quirk(info_app):
    app, _ = info_app
    client = _client(app)
    plaintext = json.dumps({
        "endpoint": "/info", "method": "GET", "body": "null",
    }).encode()
    # Use the deprecated /loki/ alias: pins that the alias still routes.
    r = client.post("/loki/v3/lsrpc", data=plaintext)
    assert r.status_code == 200
    body = json.loads(base64.b64decode(r.get_data()))
    _check_t(body.pop("t"))
    assert body == _direct_info(client)


def test_v3_subrequest_error_returns_status_code_json(make_app):
    app, _ = make_app()
    plaintext = json.dumps({"endpoint": "/info", "method": "GET"}).encode()
    r = _client(app).post("/oxen/v3/lsrpc", data=plaintext)
    assert r.status_code == 200
    assert base64.b64decode(r.get_data()) == b'{"status_code": 500}'


def test_v3_invalid_payload_returns_400_json(info_app):
    app, _ = info_app
    r = _client(app).post("/oxen/v3/lsrpc", data=b"not-json")
    assert r.status_code == 200
    assert base64.b64decode(r.get_data()) == b'{"status_code": 400}'


def test_onion_decrypt_failure_returns_http_400(info_app):
    # The stub raises when the POST body starts with FORCE-DECRYPT-FAIL.
    app, _ = info_app
    r = _client(app).post("/oxen/v4/lsrpc", data=b"FORCE-DECRYPT-FAIL")
    assert r.status_code == 400
