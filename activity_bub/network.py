"""Small bounded HTTPS client. Resolves and pins each connection to public IPs."""
from __future__ import annotations

import gzip
import http.client
import io
import ipaddress
import json
import re
import socket
import ssl
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit

from . import __version__
from .telemetry import Governor, Traffic


def domain(value: str) -> str:
    value = str(value or "").strip().lower()
    if not value or len(value) > 2048 or any(c.isspace() or ord(c)<32 or ord(c)==127 or c=="\\" for c in value):
        raise ValueError("Enter a public server domain, for example mastodon.social")
    u = urlsplit(value if "://" in value else "https://" + value)
    if u.scheme != "https" or u.username or u.password or u.port not in (None, 443):
        raise ValueError("Only public HTTPS servers on port 443 are supported")
    host = (u.hostname or "").rstrip(".").encode("idna").decode("ascii")
    if len(host) > 253 or "." not in host or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", p) for p in host.split(".")):
        raise ValueError("Not a valid public server domain")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("Use a server domain, not an IP address")
    if host.rsplit(".",1)[-1].isdigit() or host.endswith((".local", ".localhost", ".internal", ".test", ".invalid", ".onion")):
        raise ValueError("Only public internet domains are supported")
    return host


def public_addresses(host: str, port=443):
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not addresses:
        raise OSError("DNS returned no addresses")
    # Reject mixed public/private DNS answers too. Pin sockets to these exact results.
    for _, _, _, _, addr in addresses:
        ip = ipaddress.ip_address(addr[0])
        embedded=[getattr(ip,"ipv4_mapped",None),getattr(ip,"sixtofour",None)]
        if getattr(ip,"teredo",None):embedded.extend(ip.teredo)
        if isinstance(ip,ipaddress.IPv6Address) and ip in ipaddress.IPv6Network("64:ff9b::/96"):
            embedded.append(ipaddress.IPv4Address(int(ip)&0xffffffff))
        if (not ip.is_global or ip.is_multicast or ip.is_reserved
                or any(not address.is_global or address.is_multicast or address.is_reserved for address in embedded if address)):
            raise ValueError("Server resolves to a non-public address")
    return addresses


class PublicHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        failure = None
        for family, socktype, proto, _, addr in public_addresses(self.host, self.port)[:4]:
            sock = socket.socket(family, socktype, proto)
            sock.settimeout(self.timeout)
            try:
                sock.connect(addr)
                self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
                return
            except OSError as exc:
                failure = exc
                sock.close()
        raise failure or OSError("Could not connect")


class FetchError(Exception):
    def __init__(self, message, status=0, retry_after=0, restricted=False):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after
        self.restricted = restricted


def bounded_json(payload):
    # Bound nesting before parsing instead of trusting an embedding runtime's
    # potentially raised recursion limit. Quotes inside escaped strings do not
    # change nesting. Ordinary string contents are skipped by the regex engine.
    # Parse the same UTF-8 representation that the byte-level depth scan sees;
    # json.loads(bytes) otherwise auto-detects UTF-16/32 and could disagree with
    # quote/backslash handling in the scan. JSON network APIs use UTF-8.
    text=payload.decode("utf-8-sig")
    depth=0;quoted=False
    for match in re.finditer(br'[{}\[\]"]',payload):
        token=match[0]
        if token==b'"':
            escaped=0;position=match.start()-1
            while position>=0 and payload[position]==92:
                escaped+=1;position-=1
            if escaped%2==0:quoted=not quoted
        elif not quoted:
            depth += 1 if token in (b"{",b"[") else -1
            if depth>64:raise ValueError("JSON nesting exceeds limit")
    def invalid_constant(value):raise ValueError("Non-finite JSON number")
    return json.loads(text,parse_constant=invalid_constant)


def retry_seconds(value: str, now=None) -> float:
    try:
        return max(1, min(7 * 86400, float(value)))
    except (ValueError, TypeError):
        try:
            dt = parsedate_to_datetime(value)
            return max(1, min(7 * 86400, dt.timestamp() - (time.time() if now is None else now)))
        except (ValueError, TypeError, OverflowError):
            return 300


class Network:
    def __init__(self, stop: threading.Event | None = None, meter=None, governor=None):
        self.stop = stop or threading.Event()
        self._tls = ssl.create_default_context()
        self.meter=meter or Traffic()
        self.governor=governor or Governor()

    def request(self, url, data=None, max_bytes=4 * 1024 * 1024):
        for _ in range(5):
            if self.stop.is_set():
                raise FetchError("Stopped")
            u = urlsplit(url)
            host = domain(url)
            if not self.governor.wait(host,self.stop):
                raise FetchError("Stopped")
            conn = PublicHTTPSConnection(host, timeout=6, context=self._tls)
            path = u.path or "/"
            if u.query:
                path += "?" + u.query
            headers = {"User-Agent": f"ActivityBub/{__version__} (public-feed desktop reader)",
                       "Accept": "application/json, image/*;q=0.8", "Accept-Encoding": "identity"}
            body = None
            if data is not None:
                body = json.dumps(data).encode("utf-8")
                headers["Content-Type"] = "application/json"
            started=time.monotonic()
            success=False
            limited=False
            self.meter.started()
            try:
                deadline = time.monotonic() + 24
                conn.request("POST" if data is not None else "GET", path, body=body, headers=headers)
                r = conn.getresponse()
                if r.status in (301, 302, 303, 307, 308):
                    # Never replay a POST to another origin or silently change its method.
                    new = urljoin(url, r.getheader("Location", ""))
                    if data is not None and (domain(new) != host or r.status not in (307, 308)):
                        raise FetchError("API redirect rejected")
                    url = new
                    success=True
                    continue
                if not 200 <= r.status < 300:
                    detail = ""
                    try:
                        error_payload=r.read1(4096)
                        self.meter.add("bytes",len(error_payload))
                        info = bounded_json(error_payload)
                        detail = str(info.get("error") or info.get("message") or "")[:160] if isinstance(info, dict) else ""
                        detail = re.sub(r"[\x00-\x1f\x7f]"," ",detail)
                    except (ValueError, OSError, RecursionError):
                        pass
                    restricted = r.status in (401, 403) or (r.status == 422 and bool(re.search(r"authenticat|access.?token|credential", detail, re.I)))
                    limited=r.status in (429,503)
                    retry=retry_seconds(r.getheader("Retry-After","300")) if limited else 0
                    if limited:self.governor.cooldown(host,retry)
                    raise FetchError(f"HTTP {r.status}: {detail or r.reason}",r.status,retry,restricted)
                length = r.getheader("Content-Length", "")
                if length.isdigit() and int(length) > max_bytes:
                    raise FetchError("Response exceeds size limit")
                chunks, size = [], 0
                while True:
                    if self.stop.is_set():
                        raise FetchError("Stopped")
                    if time.monotonic() > deadline:
                        raise FetchError("Request exceeded time limit")
                    chunk = r.read1(min(65536, max_bytes + 1 - size))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                    self.meter.add("bytes",len(chunk))
                    if size > max_bytes:
                        raise FetchError("Response exceeds size limit")
                payload = b"".join(chunks)
                if len(payload) > max_bytes:
                    raise FetchError("Response exceeds size limit")
                if r.getheader("Content-Encoding", "").lower() == "gzip":
                    with gzip.GzipFile(fileobj=io.BytesIO(payload)) as gz:
                        payload = gz.read(max_bytes + 1)
                    if len(payload) > max_bytes:
                        raise FetchError("Expanded response exceeds size limit")
                success=True
                return payload
            except FetchError:
                raise
            except (OSError, http.client.HTTPException, ValueError) as exc:
                raise FetchError(str(exc)[:240]) from exc
            finally:
                conn.close()
                self.meter.finished(time.monotonic()-started,success,limited)
        raise FetchError("Too many redirects")

    def json(self, url, data=None):
        try:
            return bounded_json(self.request(url, data))
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise FetchError("Server did not return valid JSON") from exc
