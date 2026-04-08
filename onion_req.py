import base64
import json
import logging
import os
import traceback
from io import BytesIO
from typing import Optional, Union

import nacl.encoding
import nacl.public
from flask import Flask, abort, request
from session_util.onionreq import OnionReqParser

log = logging.getLogger("onion_req")

OK = 200
BAD_REQUEST = 400
BODY_METHODS = ("POST", "PUT")


def _load_or_generate_keypair(key_path: str = "key_x25519") -> nacl.public.PrivateKey:
    if os.path.exists(key_path):
        with open(key_path, "rb") as fh:
            raw = fh.read()
        if len(raw) != 32:
            raise RuntimeError(
                "Invalid {}: expected 32 bytes, got {}".format(key_path, len(raw))
            )
        privkey = nacl.public.PrivateKey(raw)
    else:
        privkey = nacl.public.PrivateKey.generate()
        with open(key_path, "wb") as fh:
            fh.write(privkey.encode())
        log.info("Generated new X25519 keypair and saved to %s", key_path)

    pubkey_hex = privkey.public_key.encode(encoder=nacl.encoding.HexEncoder).decode()
    log.info("Onion request server pubkey: %s", pubkey_hex)
    return privkey


def _bencode_consume_string(body: memoryview):
    """Parse a bencoded byte string from the start of body. Returns (string_view, remainder_view)."""
    pos = 0
    while pos < len(body) and 0x30 <= body[pos] <= 0x39:
        pos += 1
    if pos == 0 or pos >= len(body) or body[pos] != 0x3A:
        raise ValueError("Invalid bencoding: expected N: length prefix")
    strlen = int(body[0:pos])
    pos += 1  # skip ':'
    if pos + strlen > len(body):
        raise ValueError("Invalid bencoding: length exceeds buffer")
    return body[pos: pos + strlen], body[pos + strlen:]


def _encode_base64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _make_subrequest(
    app: Flask,
    method: str,
    path: str,
    *,
    headers=None,
    content_type: Optional[str] = None,
    body: Optional[Union[bytes, memoryview]] = None,
):
    if headers is None:
        headers = {}

    http_headers = {
        "HTTP_{}".format(h.upper().replace("-", "_")): v
        for h, v in headers.items()
    }

    if content_type is None:
        if "HTTP_CONTENT_TYPE" in http_headers:
            content_type = http_headers["HTTP_CONTENT_TYPE"]
        elif body is not None:
            content_type = "application/octet-stream"
        else:
            content_type = ""

    for key in ("HTTP_CONTENT_TYPE", "HTTP_CONTENT_LENGTH"):
        http_headers.pop(key, None)

    if body is None:
        body = b""

    if "?" in path:
        path, query_string = path.split("?", 1)
    else:
        query_string = ""

    subreq_env = {
        **request.environ,
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": query_string,
        "CONTENT_TYPE": content_type,
        "CONTENT_LENGTH": len(body),
        **http_headers,
        "wsgi.input": BytesIO(body),
        "flask._preserve_context": False,
    }

    try:
        log.debug("Subrequest: %s %s", method, path)
        with app.request_context(subreq_env):
            response = app.full_dispatch_request()
        if response.status_code != OK:
            log.warning("Subrequest %s %s returned status %s", method, path, response.status_code)
        return response, {
            k.lower(): v
            for k, v in response.get_wsgi_headers(subreq_env)
            if k.lower() != "content-length"
        }
    except Exception:
        log.warning("Subrequest %s %s failed:\n%s", method, path, traceback.format_exc())
        raise


def _handle_v3_plaintext(app: Flask, body: bytes) -> bytes:
    try:
        if not body.startswith(b"{"):
            raise RuntimeError("v3 body must be a JSON object")

        req = json.loads(body)
        endpoint = req["endpoint"]
        method = req["method"]
        subreq_headers = {k.lower(): v for k, v in req.get("headers", {}).items()}

        if method in BODY_METHODS:
            subreq_body = req.get("body", "").encode()
        else:
            subreq_body = b""
            # Android bug: some clients send body="null" on GET requests
            if req.get("body") == "null":
                pass
            elif req.get("body"):
                raise RuntimeError("Invalid {} {}: must not contain a body".format(method, endpoint))

        if not endpoint.startswith("/"):
            endpoint = "/" + endpoint

        response, _hdrs = _make_subrequest(
            app, method, endpoint,
            headers=subreq_headers,
            body=subreq_body,
            content_type="application/json",
        )

        if response.status_code == OK:
            data = response.get_data()
            log.debug("v3 subrequest %s OK, %d bytes", endpoint, len(data))
            return data

        return json.dumps({"status_code": response.status_code}).encode()

    except Exception as exc:
        log.warning("Invalid v3 onion request: %s", exc)
        return json.dumps({"status_code": BAD_REQUEST}).encode()


def _handle_v4_plaintext(app: Flask, body: bytes) -> bytes:
    try:
        if not (body.startswith(b"l") and body.endswith(b"e")):
            raise RuntimeError("v4 body must be a bencoded list")

        belems = memoryview(body)[1:-1]
        meta_view, belems = _bencode_consume_string(belems)
        meta = json.loads(meta_view.tobytes())

        if len(belems) > 1:
            subreq_body, belems = _bencode_consume_string(belems)
            if len(belems):
                raise RuntimeError("v4 body has more than 2 parts")
            subreq_body = bytes(subreq_body)
        else:
            subreq_body = b""

        method = meta["method"]
        endpoint = meta["endpoint"]
        if not endpoint.startswith("/"):
            raise RuntimeError("v4 endpoint must start with /")

        response, resp_headers = _make_subrequest(
            app, method, endpoint,
            headers=meta.get("headers", {}),
            body=subreq_body,
        )
        data = response.get_data()
        log.debug("v4 subrequest %s returned %s, %d bytes", endpoint, response.status_code, len(data))
        out_meta = {"code": response.status_code, "headers": resp_headers}

    except Exception as exc:
        log.warning("Invalid v4 onion request: %s", exc)
        out_meta = {"code": BAD_REQUEST, "headers": {"content-type": "text/plain; charset=utf-8"}}
        data = b"Invalid v4 onion request"

    meta_bytes = json.dumps(out_meta).encode()
    return (
        b"l"
        + str(len(meta_bytes)).encode() + b":" + meta_bytes
        + str(len(data)).encode() + b":" + data
        + b"e"
    )


def handle_onion_requests(app: Flask, key_path: str = "key_x25519") -> None:
    privkey = _load_or_generate_keypair(key_path)
    privkey_bytes = privkey.encode()
    pubkey_bytes = privkey.public_key.encode()

    def _decrypt() -> OnionReqParser:
        try:
            return OnionReqParser(pubkey_bytes, privkey_bytes, request.data)
        except Exception as exc:
            log.warning("Failed to decrypt onion request: %s", exc)
            abort(BAD_REQUEST)

    @app.post("/oxen/v3/lsrpc")
    @app.post("/loki/v3/lsrpc")
    def handle_v3_onion_request():
        parser = _decrypt()
        plaintext = _handle_v3_plaintext(app, parser.payload)
        return _encode_base64(parser.encrypt_reply(plaintext))

    @app.post("/oxen/v4/lsrpc")
    def handle_v4_onion_request():
        parser = _decrypt()
        response = _handle_v4_plaintext(app, parser.payload)
        return parser.encrypt_reply(response)

    log.info("Onion request endpoints registered: /oxen/v3/lsrpc, /loki/v3/lsrpc, /oxen/v4/lsrpc")
