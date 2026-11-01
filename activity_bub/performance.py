"""Bounded worker profiles and feedback-driven concurrency."""
from dataclasses import dataclass
import os
import time

import psutil


@dataclass(frozen=True)
class Profile:
    key: str
    label: str
    discovery: int
    feed: int
    history: int
    poll_seconds: float
    history_seconds: float
    buffer_target: int
    images: int

    @property
    def total(self):
        return self.discovery+self.feed+self.history


PROFILES=(Profile("quiet","Quiet",2,4,2,35,20,400,2),
          Profile("balanced","Balanced",5,8,3,18,10,900,3),
          Profile("max","MAX",10,16,6,9,4,1800,4))


def get_profile(key):
    return next((p for p in PROFILES if p.key==key),PROFILES[-1])


class AdaptiveLimit:
    def __init__(self):
        self.scale=1.0
        self.reason="Ready"
        self.last_change=0.0

    def update(self, cpu=0, rss=0, available=2**63, total=0, lag_ms=0, now=None):
        now=time.monotonic() if now is None else now
        reason=""
        if rss>700*1024*1024 or available<max(256*1024*1024,total*.04):
            reason="Memory pressure"
        elif lag_ms>250:
            reason="Keeping the UI responsive"
        elif cpu>max(120,(os.cpu_count() or 4)*15):
            reason="CPU pressure"
        if reason:
            if now-self.last_change>=2:
                self.scale=max(.25,self.scale*.75)
                self.last_change=now
            self.reason=reason
        elif now-self.last_change>=8:
            self.scale=min(1.0,self.scale+.1)
            self.last_change=now
            self.reason="Ready" if self.scale==1 else "Recovering concurrency"
        return self.scale

    def limits(self,profile):
        return {"discovery":max(1,int(profile.discovery*self.scale)),
                "feed":max(1,int(profile.feed*self.scale)),
                "history":max(1,int(profile.history*self.scale))}


class ProcessStats:
    def __init__(self):
        self.process=None
        try:
            self.process=psutil.Process()
            self.process.cpu_percent(None)
        except (psutil.Error,OSError,NotImplementedError):
            self.process=None

    def sample(self):
        try:
            if self.process is None:raise psutil.NoSuchProcess(os.getpid())
            memory=psutil.virtual_memory()
            return {"cpu":self.process.cpu_percent(None),"rss":self.process.memory_info().rss,
                    "threads":self.process.num_threads(),"available":memory.available,"total":memory.total,"resources_available":True}
        except (psutil.Error,OSError,NotImplementedError):
            return {"cpu":0,"rss":0,"threads":0,"available":2**63,"total":0,"resources_available":False}
