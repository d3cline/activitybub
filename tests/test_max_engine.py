import threading
import time

from activity_bub.engine import APIs,Engine
from activity_bub.network import FetchError
from activity_bub.performance import AdaptiveLimit,ProcessStats,get_profile
from activity_bub.store import Store
from activity_bub.telemetry import Governor,Traffic
from tests.test_core import FakeNetwork,post


def test_history_uses_its_own_timer():
    s=Store(":memory:");s.add_servers(["one.example"])
    s.update_server("one.example",state="ready",adapter="mastodon",older_cursor="100",next_feed=time.time()+3600,next_older=0)
    assert s.due("older")["host"]=="one.example"
    assert s.due("feed") is None
    s.close()


def test_network_scope_falls_back_to_public_local_without_retrying_restriction():
    raw={"id":"90","visibility":"public","uri":"https://one.example/p/90","account":{"acct":"a"},"content":"Hi"}
    net=FakeNetwork([FetchError("Login required",401,restricted=True),[raw]])
    server={"host":"one.example","adapter":"mastodon","older_cursor":"100","desired_scope":"network"}
    result=APIs(net).feed(server,older=True)
    assert result["scope"]=="local" and result["remote_denied"]
    assert "local=false" in net.calls[0][0] and "local=true" in net.calls[1][0]
    assert "max_id=100" in net.calls[1][0]
    net=FakeNetwork([[raw]])
    APIs(net).feed({**server,"remote_denied":1})
    assert len(net.calls)==1 and "local=true" in net.calls[0][0]


def test_global_rate_limit_never_triggers_a_local_fallback():
    net=FakeNetwork([FetchError("rate limit",429,300)])
    try:APIs(net).feed({"host":"one.example","adapter":"mastodon","desired_scope":"network"})
    except FetchError as exc:assert exc.status==429
    else:raise AssertionError("Expected cooldown")
    assert len(net.calls)==1


def test_empty_readable_network_is_not_locked_by_optional_local_probe():
    net=FakeNetwork([[],FetchError("Local preview disabled",403,restricted=True)])
    result=APIs(net).feed({"host":"one.example","adapter":"mastodon","desired_scope":"network"})
    assert result["posts"]==[] and result["scope"]=="network"
    assert not result["remote_denied"]


def test_live_queries_resume_from_newest_cursor():
    net=FakeNetwork([[]])
    APIs(net).feed({"host":"one.example","adapter":"mastodon","newest_cursor":"10","feed_scope":"local"})
    assert "min_id=10" in net.calls[0][0] and "limit=40" in net.calls[0][0]


def test_adaptive_budget_reduces_pressure_and_recovers_without_zero_workers():
    c=AdaptiveLimit();profile=get_profile("max")
    assert sum(c.limits(profile).values())==32
    c.update(rss=800*1024**2,now=3)
    assert sum(c.limits(profile).values())<32
    for now in (6,9,12,15,18):c.update(lag_ms=1000,now=now)
    assert all(n>=1 for n in c.limits(profile).values())
    before=c.scale;c.update(now=30)
    assert c.scale>before


def test_process_counters_can_be_unavailable_without_stopping_app(monkeypatch):
    from activity_bub import performance
    def missing():raise performance.psutil.NoSuchProcess(1)
    monkeypatch.setattr(performance.psutil,"Process",missing)
    assert not ProcessStats().sample()["resources_available"]


def test_stalled_gui_heartbeat_reduces_budget(monkeypatch):
    s=Store(":memory:");e=Engine(s)
    monkeypatch.setattr(e.process,"sample",lambda:{"cpu":0,"rss":0,"available":2**63,"total":0,"threads":1,"resources_available":True})
    e.note_ui_lag(0);e._ui_lag=(0,time.monotonic()-2)
    e._sample()
    assert sum(e.snapshot()["limits"].values())<32
    assert e.snapshot()["adaptive"]=="Keeping the UI responsive"
    s.close()


def test_traffic_counts_successes_errors_bytes_and_unique_posts(monkeypatch):
    from activity_bub import telemetry
    clock=[0.0]
    monkeypatch.setattr(telemetry.time,"monotonic",lambda:clock[0])
    meter=Traffic();meter.started();meter.add("bytes",4096);meter.add("posts",40);meter.finished(.2,True)
    clock[0]=1
    snapshot=meter.snapshot()
    assert snapshot["rps"]==1 and snapshot["bps"]==4096 and snapshot["pps"]==40
    meter.started();meter.finished(.1,False,True)
    assert meter.snapshot()["errors"]==1 and meter.snapshot()["limited"]==1
    assert meter.snapshot()["http_active"]==0


def test_shared_governor_cooldown_is_interruptible():
    governor=Governor();stop=threading.Event();governor.cooldown("one.example",60)
    entered=threading.Event();result=[]
    def worker():entered.set();result.append(governor.wait("one.example",stop))
    thread=threading.Thread(target=worker);thread.start();assert entered.wait(1)
    assert governor.wait("two.example",threading.Event())
    stop.set();thread.join(1)
    assert result==[False] and not thread.is_alive()


def test_reader_queue_accepts_later_arrivals_without_repeating_old_rows():
    s=Store(":memory:");s.insert_posts([post(1),post(2)])
    first=s.page();s.queue_reader(first)
    assert not s.page(unseen_only=True)
    s.insert_posts([post(100)])
    assert not s.page(before=(first[-1].created,first[-1].seq),unseen_only=True)
    assert [p.created for p in s.page(unseen_only=True)]==[100]
    s.queue_reader(s.page(unseen_only=True))
    assert s.unread_count()==0
    s.prune(1);s.reset_reader();assert s.unread_count()==1
    s.close()


def test_live_workers_progress_while_discovery_workers_are_blocked():
    blocked=threading.Event();live=threading.Event();release=threading.Event()
    class API:
        def probe(self,server):
            blocked.set();release.wait(2)
            return {"software":"mastodon","adapter":"mastodon","state":"ready","title":server["host"]}
        def peers(self,server):return {"hosts":[],"offset":0,"complete":True}
        def feed(self,server,older=False):
            live.set();return {"posts":[post(server=server["host"])],"cursor":"1","done":False}
    s=Store(":memory:");s.add_servers([f"d{i}.example" for i in range(20)])
    e=Engine(s,api=API());thread=threading.Thread(target=e.run);thread.start()
    assert blocked.wait(1)
    s.add_servers(["live.example"]);s.update_server("live.example",state="ready",adapter="mastodon",next_peers=time.time()+999)
    try:
        assert live.wait(1.5),"Slow discovery starved the independent feed pool"
        snapshot=e.snapshot()
        assert snapshot["jobs"]["discovery"]<=snapshot["limits"]["discovery"]
        assert sum(snapshot["jobs"].values())<=32
    finally:
        e.stop.set();release.set();thread.join(3);s.close()
    assert not thread.is_alive()
