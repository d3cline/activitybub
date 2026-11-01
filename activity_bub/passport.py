"""Local exploration rewards, based on new places and first-time saves."""
RANKS = ((0,"New Signal"),(50,"Scout"),(150,"Wanderer"),(400,"Signal Surfer"),
         (1000,"World Hopper"),(2500,"Void Cartographer"))
BADGES = (
    ("contact","First Contact","Read a post from your first server.","worlds",1),
    ("ten","Ten Worlds","Read posts from 10 different servers.","worlds",10),
    ("hopper","World Hopper","Visit 25 different servers.","worlds",25),
    ("map","Cartographer","Visit 100 different servers.","worlds",100),
    ("treasure","Good Find","Save your first post.","keeps",1),
    ("goblin","Bookmark Goblin","Save 10 distinct posts.","keeps",10),
    ("keeper","Signal Keeper","Save 50 distinct posts.","keeps",50),
    ("palette","Palette Wizard","Try 3 color themes.","themes",3),
)


def progress(worlds, keeps, themes=1):
    xp=25*worlds+10*keeps
    index=max(i for i,(threshold,_) in enumerate(RANKS) if xp>=threshold)
    start,rank=RANKS[index]
    next_threshold=RANKS[index+1][0] if index+1<len(RANKS) else None
    counts={"worlds":worlds,"keeps":keeps,"themes":themes}
    return {**counts,"xp":xp,"rank":rank,"level":index+1,"start":start,"next":next_threshold,
            "badges":[{"key":key,"name":name,"description":description,"earned":counts[field]>=target,
                       "count":counts[field],"target":target} for key,name,description,field,target in BADGES]}
