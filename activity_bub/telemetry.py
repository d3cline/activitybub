"""Thread-safe app traffic counters; no external telemetry service."""
from collections import deque
import threading
import time


class Traffic:
    def __init__(self):
        self.lock=threading.Lock()
        self.values={"requests":0,"finished":0,"errors":0,"limited":0,"bytes":0,"posts":0,"servers":0,"http_active":0}
        self.samples=deque([(time.monotonic(),0,0,0,0)],maxlen=120)
        self.latencies=deque(maxlen=128)

    def add(self,key,count):
        with self.lock:
            self.values[key]+=count

    def started(self):
        with self.lock:
            self.values["requests"]+=1
            self.values["http_active"]+=1

    def finished(self,seconds,success,limited=False):
        with self.lock:
            self.values["finished"]+=1
            self.values["http_active"]-=1
            self.values["errors"]+=int(not success)
            self.values["limited"]+=int(limited)
            self.latencies.append(seconds)

    def snapshot(self):
        now=time.monotonic()
        with self.lock:
            v=dict(self.values)
            if now-self.samples[-1][0]>=.1:
                self.samples.append((now,v["finished"],v["bytes"],v["posts"],v["servers"]))
            while len(self.samples)>2 and self.samples[1][0]<now-5:
                self.samples.popleft()
            first=self.samples[0]
            elapsed=max(.1,now-first[0])
            v.update(rps=(v["finished"]-first[1])/elapsed,bps=(v["bytes"]-first[2])/elapsed,
                     pps=(v["posts"]-first[3])/elapsed,sps=(v["servers"]-first[4])/elapsed,
                     latency_ms=1000*sum(self.latencies)/len(self.latencies) if self.latencies else 0)
            return v


class Governor:
    """One shared per-host gate for API and media requests, including cooldowns."""
    def __init__(self,requests_per_second=48):
        self.lock=threading.Lock()
        self.next_host={}
        self.cooldowns={}
        self.next_global=0.0
        self.interval=1/requests_per_second
        self.count=0

    def cooldown(self,host,seconds):
        with self.lock:
            self.cooldowns[host]=max(self.cooldowns.get(host,0),time.monotonic()+seconds)

    def wait(self,host,stop):
        while not stop.is_set():
            with self.lock:
                now=time.monotonic()
                due=max(self.next_global,self.next_host.get(host,0),self.cooldowns.get(host,0))
                if due<=now:
                    self.next_global=now+self.interval
                    self.next_host[host]=now+1
                    self.count+=1
                    if self.count%512==0:
                        self.next_host={h:t for h,t in self.next_host.items() if t>now-60}
                        self.cooldowns={h:t for h,t in self.cooldowns.items() if t>now}
                    return True
            stop.wait(min(.5,max(.001,due-now)))
        return False
