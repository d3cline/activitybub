import json
import socket
import threading
import time
from datetime import datetime, timezone

import pytest

from activity_bub.content import Post, mastodon_post, misskey_post, plain, rich_text, timestamp
from activity_bub.engine import APIs, Engine
from activity_bub.network import FetchError, Network, domain, public_addresses, retry_seconds
from activity_bub.store import Store


def post(n=1, created=None, server="one.example", **kwargs):
    return Post(uri=f"https://{server}/p/{n}", remote_id=str(n), server=server, author="Bub",
                handle="bub@" + server, text="Hello, space wizard", url=f"https://{server}/p/{n}",
                created=n if created is None else created, **kwargs)


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


def test_html_is_plain_and_no_remote_resource_is_rendered():
    text = plain('<p>Hello <b>world</b><img src="http://internal" alt=":bub:"></p><script>bad()</script>')
    assert text == "Hello world:bub:"
    assert "<img" not in rich_text(text)
    assert '<a href="https://example.org">' in rich_text("visit https://example.org")
    assert "&lt;script&gt;" in rich_text("<script>")


@pytest.mark.parametrize("bad", ["localhost", "https://127.0.0.1", "10.0.0.1", "http://example.com", "https://u:p@example.com", "example.com:8443", "a.local", "a.onion", "-bad.example", "https://[::1]", "hello there.example"])
def test_domains_reject_private_targets_and_credentials(bad):
    with pytest.raises(ValueError):
        domain(bad)


def test_domain_idna_and_url_normalization():
    assert domain("HTTPS://MASTODON.SOCIAL/@hello") == "mastodon.social"
    assert domain("bücher.example") == "xn--bcher-kva.example"


def test_dns_mixed_answers_rejected(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))])
    with pytest.raises(ValueError, match="non-public"):
        public_addresses("one.example")


@pytest.mark.parametrize("address", ["169.254.169.254", "224.0.0.1", "ff02::1", "::1", "fc00::1"])
def test_dns_special_networks_rejected(monkeypatch, address):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))])
    with pytest.raises(ValueError, match="non-public"):
        public_addresses("one.example")


def test_rate_limit_dates_and_offsets():
    assert retry_seconds("120") == 120
    assert retry_seconds("Thu, 01 Jan 1970 00:02:00 GMT", now=0) == 120
    assert retry_seconds("nonsense") == 300
    assert timestamp("2026-01-01T01:00:00-07:00") == timestamp("2026-01-01T08:00:00Z")


def test_public_post_only_and_canonical_dedupe(store):
    raw = {"id": "1", "visibility": "public", "uri": "https://one.example/objects/1",
           "url": "https://one.example/@bub/1", "account": {"acct": "bub@one.example"}, "content": "<p>Hello</p>"}
    a = mastodon_post(raw, "one.example")
    b = mastodon_post({**raw, "id": "200"}, "two.example")
    assert store.insert_posts([a, b]) == 1
    assert mastodon_post({**raw, "visibility": "private"}, "one.example") is None
    assert mastodon_post({**raw, "visibility": "unlisted"}, "one.example") is None
    assert a.text == "Hello" and a.handle == "bub@one.example"


def test_misskey_public_and_local_only():
    raw = {"id": "abc123", "visibility": "public", "text": "Hi **bub**", "user": {"username": "bub"},
           "files": [{"url": "https://cdn.example/1.jpg", "isSensitive": True, "type": "image/jpeg"}], "reactions": {"x": 2}}
    p = misskey_post(raw, "one.example")
    assert p.url == "https://one.example/notes/abc123"
    assert p.sensitive and p.likes == 2
    assert misskey_post({**raw, "localOnly": True}, "one.example") is None
    assert misskey_post({**raw, "visibility": "followers"}, "one.example") is None


def test_chronological_pagination_accepts_late_arriving_history(store):
    store.add_servers(["one.example"])
    store.insert_posts([post(n) for n in range(100, 110)])
    first = store.page(limit=5)
    assert [p.created for p in first] == [109, 108, 107, 106, 105]
    second = store.page(before=(first[-1].created, first[-1].seq), limit=5)
    cursor = (second[-1].created, second[-1].seq)
    # Backfilled older content is inserted AFTER the currently displayed rows.
    store.insert_posts([post(98), post(99)])
    assert [p.created for p in store.page(before=cursor)] == [99, 98]
    store.insert_posts([post(500)])
    assert [p.created for p in store.page(before=cursor)] == [99, 98]


def test_equal_timestamps_paginate_without_duplicates(store):
    store.insert_posts([post(n, created=100) for n in range(10)])
    a = store.page(limit=5)
    b = store.page(before=(a[-1].created, a[-1].seq), limit=5)
    assert len({p.uri for p in a + b}) == 10


def test_filters_bookmarks_mutes_and_pruning(store):
    store.add_servers(["one.example", "two.example"])
    store.insert_posts([post(1, language="en"), post(2, language="ja", sensitive=True),
                        post(3, server="two.example", media=[{"url": "https://a.example/1"}])])
    assert len(store.page(language="en")) == 1
    assert len(store.page(language="__unknown__")) == 1
    assert len(store.page(hide_sensitive=True)) == 2
    assert len(store.page(media_only=True)) == 1
    assert len(store.page(muted_words="space wizard")) == 0
    assert len(store.page(query="SPACE")) == 3
    first = store.page()[-1]
    store.save_post(first.seq, True)
    store.prune(keep=1)
    assert store.stats()["posts"] == 2 and len(store.page(saved_only=True)) == 1
    store.update_server("two.example", muted=1)
    assert len(store.page()) == 1
    store.mute_author("bub@one.example")
    assert not store.page()
    store.unmute_author("bub@one.example")
    assert len(store.page()) == 1


def test_state_and_settings_survive_restart(tmp_path):
    path = tmp_path / "bub.sqlite"
    s = Store(path)
    s.add_servers(["one.example"])
    s.update_server("one.example", older_cursor="old-id", state="ready", adapter="mastodon")
    s.set_setting("muted_words", "spoilers")
    s.insert_posts([post()])
    s.save_post(s.page()[0].seq, True)
    s.close()
    s = Store(path)
    assert s.server("one.example")["older_cursor"] == "old-id"
    assert s.setting("muted_words") == "spoilers" and s.page()[0].saved
    s.close()


class FakeNetwork:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def json(self, url, data=None):
        self.calls.append((url, data))
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def test_adapter_mastodon_history_ids_from_raw_not_filtered():
    net = FakeNetwork([[{"id": "90", "visibility": "private"}]])
    result = APIs(net).feed({"host": "one.example", "adapter": "mastodon", "older_cursor": "100"}, older=True)
    assert not result["posts"] and result["cursor"] == "90" and not result["done"]
    assert "max_id=100" in net.calls[0][0] and "local=true" in net.calls[0][0]


def test_adapter_misskey_cursor_and_peer_offset():
    net = FakeNetwork([[], [{"host": f"h{i}.example"} for i in range(100)]])
    api = APIs(net)
    s = {"host": "one.example", "adapter": "misskey", "older_cursor": "abc", "peer_offset": 100}
    assert api.feed(s, older=True)["done"]
    assert net.calls[0][1]["untilId"] == "abc"
    r = api.peers(s)
    assert r["offset"] == 200 and not r["complete"]
    assert net.calls[1][1]["sort"] == "+firstRetrievedAt"


def test_probe_uses_advertised_nodeinfo_link():
    net = FakeNetwork([{"links": [{"rel": "http://nodeinfo.diaspora.software/ns/schema/2.0", "href": "https://one.example/n/2"}]}, {"software": {"name": "sharkey"}}])
    result = APIs(net).probe({"host": "one.example"})
    assert result["adapter"] == "misskey" and net.calls[1][0].endswith("/n/2")


def test_probe_does_not_retry_after_rate_limit():
    net = FakeNetwork([FetchError("rate limited", 429, 120)])
    with pytest.raises(FetchError):
        APIs(net).probe({"host": "one.example"})
    assert len(net.calls) == 1


def test_scheduler_respects_cooldown_mutes_pause_and_discovery(store):
    store.add_servers(["one.example", "two.example"])
    e = Engine(store)
    store.update_server("one.example", state="ready", adapter="mastodon", cooldown=time.time()+300)
    assert e.choose(set())[1]["host"] == "two.example"
    store.update_server("two.example", muted=1)
    assert e.choose(set()) is None
    store.update_server("one.example", cooldown=0)
    e.discovery.clear()
    assert e.choose(set())[0] == "feed"
    assert e.choose({"one.example"}) is None


def test_locked_422_and_rate_limited_servers_are_classified(store):
    store.add_servers(["one.example"])
    e = Engine(store)
    s = store.server("one.example")
    e.failed("feed", s, FetchError("Requires an authenticated user", 422, restricted=True))
    assert store.server("one.example")["state"] == "locked"
    e.failed("peers", s, FetchError("Slow down", 429, 900))
    assert store.server("one.example")["cooldown"] > time.time() + 899


def test_discovery_dedupe_and_inflight_mute(store):
    store.add_servers(["one.example"])
    e = Engine(store)
    s = store.server("one.example")
    e.apply("peers", s, {"hosts": ["two.example", "two.example", "localhost", "127.0.0.1"], "offset": 0, "complete": True})
    assert store.stats()["servers"] == 2
    store.update_server("one.example", muted=1)
    e.apply("feed", s, {"posts": [post()], "cursor": "1", "done": False})
    assert not store.stats()["posts"]


def test_history_cursor_not_overwritten_by_fresh_posts(store):
    store.add_servers(["one.example"])
    store.update_server("one.example", adapter="mastodon", state="ready", older_cursor="50")
    e = Engine(store)
    e.apply("feed", store.server("one.example"), {"posts": [post(100)], "cursor": "90", "done": False})
    assert store.server("one.example")["older_cursor"] == "50"
    e.apply("older", store.server("one.example"), {"posts": [post(40)], "cursor": "40", "done": False})
    assert store.server("one.example")["older_cursor"] == "40"


def test_network_rejects_redirect_to_private_before_connect(monkeypatch):
    from activity_bub import network
    class Response:
        status = 302
        def getheader(self, key, default=""):
            return "https://127.0.0.1/admin" if key == "Location" else default
    class Connection:
        def __init__(self, *a, **kw): pass
        def request(self, *a, **kw): pass
        def getresponse(self): return Response()
        def close(self): pass
    monkeypatch.setattr(network, "PublicHTTPSConnection", Connection)
    with pytest.raises(ValueError):
        Network().request("https://one.example/")


def test_network_bounded_responses_and_restricted_body(monkeypatch):
    from activity_bub import network
    class Response:
        status, reason = 422, "Unprocessable Entity"
        def getheader(self, key, default=""): return default
        def read1(self, size): return b'{"error":"This method requires an authenticated user"}'
    class Connection:
        def __init__(self, *a, **kw): pass
        def request(self, *a, **kw): pass
        def getresponse(self): return Response()
        def close(self): pass
    monkeypatch.setattr(network, "PublicHTTPSConnection", Connection)
    with pytest.raises(FetchError) as raised:
        Network().request("https://one.example/")
    assert raised.value.restricted and raised.value.status == 422
    Response.status = 200
    Response.read1 = lambda self, size: b"x" * size
    with pytest.raises(FetchError, match="size limit"):
        Network().request("https://one.example/", max_bytes=1024)


def test_engine_runs_discovery_and_ingestion_offline(store):
    class API:
        def probe(self, s): return {"software": "mastodon", "adapter": "mastodon", "state": "ready", "title": s["host"]}
        def feed(self, s, older=False): return {"posts": [post(server=s["host"])], "cursor": "1", "done": True}
        def peers(self, s): return {"hosts": ["two.example"], "offset": 0, "complete": True}
    store.add_servers(["one.example"])
    e = Engine(store, api=API())
    thread = threading.Thread(target=e.run)
    thread.start()
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline and store.stats()["posts"] < 2:
        time.sleep(.02)
    e.stop.set()
    thread.join(timeout=3)
    assert not thread.is_alive()
    assert store.stats()["servers"] == 2 and store.stats()["posts"] == 2
