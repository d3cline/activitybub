"""Offline scheduling comparison; never contacts public servers."""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from activity_bub.content import Post
from activity_bub.engine import Engine
from activity_bub.performance import Profile,get_profile
from activity_bub.store import Store


class FakeAPI:
    def __init__(self):self.counts={}
    def probe(self,s):
        time.sleep(.04)
        return {"software":"mastodon","adapter":"mastodon","state":"ready","title":s["host"]}
    def peers(self,s):
        time.sleep(.04)
        return {"hosts":[],"offset":0,"complete":True}
    def feed(self,s,older=False):
        time.sleep(.04)
        host=s["host"];n=self.counts.get(host,0)+1;self.counts[host]=n
        posts=[Post(uri=f"https://{host}/p/{n}-{i}",remote_id=str(n*100+i),server=host,author="Fixture",handle="fixture@"+host,
                    text="Synthetic benchmark post",url=f"https://{host}/p/{n}-{i}",created=time.time()-i) for i in range(40)]
        return {"posts":posts,"cursor":str(n),"done":False,"raw_count":40}


def run(profile):
    s=Store(":memory:");s.add_servers([f"h{i}.example" for i in range(160)])
    e=Engine(s,api=FakeAPI());e.profile=profile;e._limits=e.controller.limits(profile)
    e._sample=lambda:None  # Isolate scheduler throughput from host resource variability.
    started=time.monotonic();thread=threading.Thread(target=e.run);thread.start()
    deadline=started+2
    while time.monotonic()<deadline:time.sleep(.05)
    e.stop.set();thread.join();elapsed=time.monotonic()-started;stats=s.stats();s.close()
    return stats["posts"],stats["ready"],elapsed


if __name__=="__main__":
    old=run(Profile("baseline","4-job baseline",1,2,1,9,4,1800,2))
    new=run(get_profile("max"))
    for label,result in (("4-job baseline",old),("MAX 32-job budget",new)):
        posts,ready,seconds=result
        print(f"{label}: {posts:,} unique synthetic posts, {ready} readable servers in {seconds:.2f}s ({posts/seconds:.0f} posts/s)")
    print(f"Synthetic post-throughput ratio: {(new[0]/new[2])/(old[0]/old[2]):.2f}x")
