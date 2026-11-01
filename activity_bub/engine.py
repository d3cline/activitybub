from __future__ import annotations

import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from collections import deque
from urllib.parse import urlencode

from .content import mastodon_post, misskey_post, plain, web_url
from .network import FetchError, Network
from .performance import AdaptiveLimit, ProcessStats, get_profile
from .telemetry import Traffic

MISSKEY = {"misskey", "sharkey", "firefish", "calckey", "iceshrimp", "cherrypick", "foundkey", "meisskey"}
MASTODON = {"mastodon", "pleroma", "akkoma", "gotosocial", "pixelfed", "friendica", "hometown", "glitch-soc", "snac"}


class APIs:
    def __init__(self, network):
        self.net = network

    def probe(self, server):
        host = server["host"]
        base = "https://" + host
        software = ""
        try:
            index = self.net.json(base + "/.well-known/nodeinfo")
            for link in index.get("links", [])[:10]:
                if "nodeinfo.diaspora.software/ns/schema/2." in str(link.get("rel", "")):
                    href = web_url(link.get("href"))
                    if href:
                        info = self.net.json(href)
                        software = plain(info.get("software", {}).get("name", "")).lower()[:80]
                        break
        except FetchError as exc:
            if exc.status in (429, 503):
                raise
        if software in MISSKEY or any(software.startswith(n + "-") for n in MISSKEY):
            return {"software": software, "adapter": "misskey", "title": host, "state": "ready"}
        if software in MASTODON:
            return {"software": software, "adapter": "mastodon", "title": host, "state": "ready"}
        if software:
            return {"software": software, "adapter": "", "title": host, "state": "unsupported"}
        for path in ("/api/v2/instance", "/api/v1/instance"):
            try:
                info = self.net.json(base + path)
                if isinstance(info, dict) and (info.get("version") or info.get("uri") or info.get("domain")):
                    return {"software": "mastodon-compatible", "adapter": "mastodon",
                            "title": plain(info.get("title") or host)[:200], "state": "ready"}
            except FetchError as exc:
                if exc.status not in (400, 404, 405, 410):
                    raise
        return {"software": "unknown", "adapter": "", "title": host, "state": "unsupported"}

    def peers(self, server):
        base = "https://" + server["host"]
        if server["adapter"] == "misskey":
            raw = self.net.json(base + "/api/federation/instances", {"limit": 100, "offset": server["peer_offset"], "sort": "+firstRetrievedAt"})
            if not isinstance(raw, list):
                raise FetchError("Unexpected peer response")
            # offset is kept on disk; the next pass explores the next page.
            return {"hosts": [p["host"] for p in raw if isinstance(p, dict) and isinstance(p.get("host"), str)],
                    "offset": server["peer_offset"] + len(raw) if len(raw) >= 100 else 0,
                    "complete": len(raw) < 100}
        raw = self.net.json(base + "/api/v1/instance/peers")
        if not isinstance(raw, list):
            raise FetchError("Unexpected peer response")
        return {"hosts": [p for p in raw if isinstance(p, str)], "offset": 0, "complete": True}

    def feed(self, server, older=False):
        host=server["host"]
        desired=server.get("desired_scope","local")
        scope="network" if desired=="network" and not server.get("remote_denied") else "local"
        previous_scope=server.get("feed_scope","local")
        denied=bool(server.get("remote_denied",False))

        def fetch(selected):
            same=selected==previous_scope
            cursor=server.get("older_cursor","") if older and same else ""
            newest=server.get("newest_cursor","") if not older and same else ""
            if server["adapter"]=="misskey":
                args={"limit":60}
                if cursor:args["untilId"]=cursor
                if newest:args["sinceId"]=newest
                channel="global-timeline" if selected=="network" else "local-timeline"
                return self.net.json(f"https://{host}/api/notes/{channel}",args)
            args={"local":"false" if selected=="network" else "true","limit":"40"}
            if cursor:args["max_id"]=cursor
            if newest:args["min_id"]=newest
            return self.net.json(f"https://{host}/api/v1/timelines/public?{urlencode(args)}")

        try:
            raw=fetch(scope)
        except FetchError as exc:
            if scope!="network" or not (exc.status in (401,403) or exc.restricted):
                raise
            # Local preview is a separate public API capability; restricted
            # network previews remain restricted and are not retried every poll.
            scope="local";denied=True
            raw=fetch(scope)
        if scope=="network" and raw==[] and not server.get("newest_cursor"):
            try:local=fetch("local")
            except FetchError as exc:
                if exc.status in (429,503):raise
                local=[]  # An optional empty-feed fallback cannot lock a readable network feed.
            if local:
                raw=local;scope="local";denied=True
        if not isinstance(raw,list):raise FetchError("Unexpected timeline response")
        parse=misskey_post if server["adapter"]=="misskey" else mastodon_post
        posts=[]
        for record in raw[:100]:
            try:
                post=parse(record,host)
                if post:posts.append(post)
            except (ValueError,TypeError,KeyError,AttributeError):continue
        last=str(raw[-1].get("id") or "")[:128] if raw and isinstance(raw[-1],dict) else ""
        newest=str(raw[0].get("id") or "")[:128] if raw and isinstance(raw[0],dict) else ""
        cursor=server.get("older_cursor","") if scope==previous_scope else ""
        return {"posts":list(reversed(posts)),"cursor":last,"newest":newest,"scope":scope,
                "remote_denied":int(denied),"reset":scope!=previous_scope,"raw_count":len(raw),
                "done":not raw or not last or (older and last==cursor)}


class Engine:
    """Independent discovery/live/history pools, globally bounded by the profile."""
    def __init__(self,store,notify=lambda *args:None,api=None):
        self.store=store
        self.notify=notify
        self.stop=threading.Event()
        self.paused=threading.Event()
        self.discovery=threading.Event();self.discovery.set()
        self.history=threading.Event()
        self.meter=Traffic()
        self.network=Network(self.stop,meter=self.meter)
        self.api=api or APIs(self.network)
        self.profile=get_profile(store.setting("power_profile","max"))
        self.scope=store.setting("feed_scope","network")
        self.controller=AdaptiveLimit()
        self.process=ProcessStats()
        self._lock=threading.RLock()
        self._runtime=self.process.sample()
        self._jobs={"discovery":0,"feed":0,"history":0}
        self._limits=self.controller.limits(self.profile)
        self._ui_lag=(0,0)
        self._unread=0
        self._buffer_low=True
        self._last_sample=0
        self._turns={}
        self._queues={k:deque() for k in ("feed","probe","peers","older")}
        self._loaded={k:0 for k in self._queues}
        self._ingested=0
        self._batch=False
        self._events={"posts":0,"history":0}
        self._log=""
        self._last_emit=0

    def set_profile(self,key):
        with self._lock:
            self.profile=get_profile(key)
            self._limits=self.controller.limits(self.profile)
            self.store.set_setting("power_profile",self.profile.key)

    def set_scope(self,scope):
        with self._lock:
            self.scope="network" if scope=="network" else "local"
            self.store.set_setting("feed_scope",self.scope)

    def note_ui_lag(self,milliseconds):
        with self._lock:self._ui_lag=(max(0,milliseconds),time.monotonic())

    def snapshot(self):
        with self._lock:
            return {**self.meter.snapshot(),**self._runtime,"jobs":dict(self._jobs),"limits":dict(self._limits),
                    "profile":self.profile.key,"scope":self.scope,"unread":self._unread,
                    "buffer_target":self.profile.buffer_target,"adaptive":self.controller.reason,
                    "scale":self.controller.scale,"paused":self.paused.is_set()}

    def _emit(self,kind,value):
        if not self._batch:self.notify(kind,value)
        elif kind in self._events:self._events[kind]+=value
        else:self._log=str(value)

    def _flush(self,force=False):
        now=time.monotonic()
        if not force and now-self._last_emit<.35:return
        self._last_emit=now
        for kind,count in self._events.items():
            if count:self.notify(kind,count)
        self._events={"posts":0,"history":0}
        if self._log:self.notify("log",self._log);self._log=""

    def _sample(self):
        now=time.monotonic()
        if now-self._last_sample<1:return
        self._last_sample=now
        runtime=self.process.sample()
        unread=self.store.unread_count()
        with self._lock:
            self._runtime=runtime
            self._unread=unread
            lag=max(self._ui_lag[0],max(0,now-self._ui_lag[1]-.25)*1000) if self._ui_lag[1] else 0
            self.controller.update(**{k:runtime[k] for k in ("cpu","rss","available","total")},lag_ms=lag,now=now)
            self._limits=self.controller.limits(self.profile)
            self._buffer_low=unread<self.profile.buffer_target

    def _candidate(self,kind,active):
        queue=self._queues[kind]
        now=time.time()
        if not queue:
            if time.monotonic()-self._loaded[kind]<.5:return None
            queue.extend(self.store.due_many(kind,active))
            self._loaded[kind]=time.monotonic()
        while queue:
            old=queue.popleft()
            if old["host"] in active:continue
            row=self.store.server(old["host"])
            if not row or row["muted"] or row["cooldown"]>now:continue
            if kind=="probe":valid=row["state"] in ("queued","error","locked","unsupported") and row["next_probe"]<=now
            elif kind=="peers":valid=bool(row["adapter"]) and row["next_peers"]<=now
            elif kind=="older":valid=row["state"]=="ready" and not row["older_done"] and bool(row["older_cursor"]) and row["next_older"]<=now
            else:valid=row["state"]=="ready" and row["next_feed"]<=now
            if valid:return row
        return None

    def choose(self,active,group=None):
        kinds={"feed":["feed"],"discovery":["probe","peers"],"history":["older"]}.get(group,["feed","probe","peers","older"])
        turn=self._turns.get(group,0)
        for offset in range(len(kinds)):
            index=(turn+offset)%len(kinds);kind=kinds[index]
            if kind in ("probe","peers") and not self.discovery.is_set():continue
            if kind=="older" and not (self.history.is_set() or self._buffer_low):continue
            server=self.store.due(kind,active) if group is None else self._candidate(kind,active)
            if server:
                self._turns[group]=(index+1)%len(kinds)
                return kind,server
        return None

    def execute(self,kind,server):
        if kind=="probe":return self.api.probe(server)
        if kind=="peers":return self.api.peers(server)
        with self._lock:scope=self.scope
        return self.api.feed({**server,"desired_scope":scope},older=kind=="older")

    def apply(self,kind,server,result):
        host=server["host"];now=time.time()
        current=self.store.server(host)
        if not current or current["muted"]:return
        if kind=="probe":
            self.store.update_server(host,**result,checked=now,failures=0,error="",next_probe=now+86400,next_feed=now,next_peers=now)
            self._loaded["feed"]=self._loaded["peers"]=0
            self._emit("log",f"{host} · {result['software']} · {result['state']}")
        elif kind=="peers":
            peers=list(result["hosts"]);random.shuffle(peers)
            count=self.store.add_servers(peers,host);self.meter.add("servers",count)
            self.store.update_server(host,last_peers=now,peer_offset=result["offset"],next_peers=now+(86400 if result["complete"] else 3))
            self._loaded["probe"]=0
            self._emit("log",f"{host} · {count:,} new servers")
        else:
            posts=result["posts"]
            count=self.store.insert_posts(posts);self.meter.add("posts",count);self._ingested+=count
            with self._lock:profile=self.profile
            values={"failures":0,"error":"","post_count":current["post_count"]+count}
            if kind=="older":
                values.update(last_older=now,next_older=now+profile.history_seconds,
                              older_cursor=result["cursor"] or current["older_cursor"],older_done=int(result["done"]))
            else:
                busy=result.get("raw_count",0)>=40
                values.update(last_feed=now,next_feed=now+(2 if busy else profile.poll_seconds*random.uniform(.8,1.4)))
                if not current["older_cursor"] and result["cursor"]:values["older_cursor"]=result["cursor"]
            if result.get("newest") and (kind!="older" or not current["newest_cursor"]):values["newest_cursor"]=result["newest"]
            if "scope" in result:
                values.update(feed_scope=result["scope"],remote_denied=result["remote_denied"])
                if result.get("reset"):
                    values.update(older_cursor=result["cursor"],newest_cursor=result["newest"],older_done=int(result["done"]) if kind=="older" else 0,next_older=now)
            self.store.update_server(host,**values)
            self._loaded["older"]=0
            if self.discovery.is_set():
                new=self.store.add_servers([p.handle.rsplit("@",1)[-1] for p in posts if "@" in p.handle],host)
                self.meter.add("servers",new)
                if new:self._loaded["probe"]=0
            if count:
                self._emit("history" if kind=="older" else "posts",count)
                self._emit("log",f"{host} · +{count} posts")
            if self._ingested>=2000:
                self.store.prune();self._ingested=0

    def failed(self,kind,server,exc):
        now=time.time();status=getattr(exc,"status",0);retry=getattr(exc,"retry_after",0)
        failures=min(server["failures"]+1,12)
        delay=retry or min(6*3600,60*2**min(failures,8))
        values={"error":str(exc)[:240]}
        if status in (429,503):values["cooldown"]=now+max(60,delay)
        if kind=="peers":values.update(next_peers=now+max(delay,3600),last_peers=now)
        elif status in (401,403) or getattr(exc,"restricted",False):
            values.update(state="locked",next_probe=now+86400,next_feed=now+86400,failures=failures)
        elif kind=="probe":values.update(state="error",next_probe=now+delay,failures=failures)
        elif status in (404,405,410,422):values.update(state="unsupported",next_probe=now+86400,next_feed=now+86400,failures=failures)
        elif kind=="older":values.update(next_older=now+delay,failures=failures)
        else:values.update(next_feed=now+delay,failures=failures)
        self.store.update_server(server["host"],**values)
        self._emit("log",f"{server['host']} · {str(exc)[:100]}")

    def run(self):
        pending={};self._batch=True
        maximum=get_profile("max")
        with ExitStack() as stack:
            pools={name:stack.enter_context(ThreadPoolExecutor(max_workers=getattr(maximum,name),thread_name_prefix="bub-"+name))
                   for name in ("discovery","feed","history")}
            try:
                while not self.stop.is_set():
                    apply_deadline=time.perf_counter()+.025
                    for future,(group,kind,server) in list(pending.items()):
                        if future.done():
                            del pending[future]
                            try:self.apply(kind,server,future.result())
                            except Exception as exc:self.failed(kind,server,exc)
                            if time.perf_counter()>apply_deadline:break
                    self._sample()
                    counts={name:0 for name in pools}
                    for group,_,_ in pending.values():counts[group]+=1
                    if not self.paused.is_set():
                        active={server["host"] for _,_,server in pending.values()}
                        with self._lock:limits=dict(self._limits)
                        for group in ("feed","discovery","history"):
                            while counts[group]<limits[group]:
                                job=self.choose(active,group)
                                if not job:break
                                kind,server=job
                                pending[pools[group].submit(self.execute,kind,server)]=(group,kind,server)
                                active.add(server["host"]);counts[group]+=1
                    with self._lock:self._jobs=counts
                    self._flush()
                    self.stop.wait(.025)
            finally:
                for future in pending:future.cancel()
                self._flush(True)
        with self._lock:self._jobs={name:0 for name in pools}
