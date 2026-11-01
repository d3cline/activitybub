from __future__ import annotations

import json
import hashlib
import sqlite3
import threading
import time
from pathlib import Path

from .content import Post
from .network import domain

SEEDS = ["mastodon.social", "mastodon.online", "fosstodon.org", "hachyderm.io", "mstdn.social",
         "infosec.exchange", "techhub.social", "mas.to", "universeodon.com", "mastodon.world",
         "social.vivaldi.net", "mastodon.art", "misskey.io", "misskey.design", "sharkey.world", "opalstack.social"]


class Store:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(path), check_same_thread=False, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS servers (
                host TEXT PRIMARY KEY, source TEXT DEFAULT '', software TEXT DEFAULT '',
                adapter TEXT DEFAULT '', title TEXT DEFAULT '', state TEXT DEFAULT 'queued',
                first_seen REAL NOT NULL, checked REAL DEFAULT 0, next_probe REAL DEFAULT 0,
                next_feed REAL DEFAULT 0, next_peers REAL DEFAULT 0, peer_offset INTEGER DEFAULT 0,
                older_cursor TEXT DEFAULT '', older_done INTEGER DEFAULT 0,
                last_feed REAL DEFAULT 0, last_peers REAL DEFAULT 0, last_older REAL DEFAULT 0,
                failures INTEGER DEFAULT 0, error TEXT DEFAULT '', muted INTEGER DEFAULT 0,
                pinned INTEGER DEFAULT 0, post_count INTEGER DEFAULT 0, cooldown REAL DEFAULT 0);
            CREATE INDEX IF NOT EXISTS server_probe ON servers(muted, state, next_probe, first_seen);
            CREATE INDEX IF NOT EXISTS server_feed ON servers(muted, state, cooldown, next_feed, last_feed);
            CREATE INDEX IF NOT EXISTS server_peers ON servers(muted, next_peers, last_peers);
            CREATE TABLE IF NOT EXISTS posts (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, uri TEXT NOT NULL UNIQUE, server TEXT NOT NULL,
                author TEXT NOT NULL, text TEXT NOT NULL, language TEXT DEFAULT '', media INTEGER DEFAULT 0,
                sensitive INTEGER DEFAULT 0, payload TEXT NOT NULL, saved INTEGER DEFAULT 0,
                created REAL DEFAULT 0);
            CREATE INDEX IF NOT EXISTS posts_saved ON posts(saved, seq);
            CREATE INDEX IF NOT EXISTS posts_server ON posts(server, seq);
            CREATE TABLE IF NOT EXISTS muted_authors (handle TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS passport_worlds (host TEXT PRIMARY KEY, first_visit REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS passport_keeps (post_key TEXT PRIMARY KEY, first_save REAL NOT NULL);
            PRAGMA user_version=1;
        """)
        columns = {r[1] for r in self.db.execute("PRAGMA table_info(posts)")}
        if "created" not in columns:
            self.db.execute("ALTER TABLE posts ADD COLUMN created REAL DEFAULT 0")
            for row in self.db.execute("SELECT seq,payload FROM posts").fetchall():
                self.db.execute("UPDATE posts SET created=? WHERE seq=?", (json.loads(row["payload"]).get("created", 0), row["seq"]))
        self.db.execute("CREATE INDEX IF NOT EXISTS posts_chronology ON posts(created DESC,seq DESC)")
        columns = {r[1] for r in self.db.execute("PRAGMA table_info(servers)")}
        for name, definition in {"next_older":"REAL DEFAULT 0", "newest_cursor":"TEXT DEFAULT ''", "feed_scope":"TEXT DEFAULT 'local'", "remote_denied":"INTEGER DEFAULT 0"}.items():
            if name not in columns:self.db.execute(f"ALTER TABLE servers ADD COLUMN {name} {definition}")
        self.db.executescript("""PRAGMA temp_store=FILE;
            CREATE TEMP TABLE IF NOT EXISTS reader_seen(uri TEXT PRIMARY KEY);
            CREATE INDEX IF NOT EXISTS server_older ON servers(muted,state,next_older,last_older);
        """)
        self.db.commit()

    def close(self):
        with self.lock:
            self.db.close()

    def setting(self, key, default=None):
        with self.lock:
            row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set_setting(self, key, value):
        with self.lock, self.db:
            self.db.execute("INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))

    def add_servers(self, hosts, source="manual"):
        count=0
        batch=[]
        def flush():
            nonlocal count
            with self.lock,self.db:
                before=self.db.total_changes
                self.db.executemany("INSERT OR IGNORE INTO servers(host,source,first_seen) VALUES (?,?,?)",batch)
                count+=self.db.total_changes-before
            batch.clear()
        for candidate in hosts:
            try:host=domain(candidate)
            except (ValueError,UnicodeError):continue
            batch.append((host,source[:253],time.time()))
            if len(batch)>=256:flush()
        if batch:flush()
        return count

    def server(self, host):
        with self.lock:
            r = self.db.execute("SELECT * FROM servers WHERE host=?", (host,)).fetchone()
            return dict(r) if r else None

    def update_server(self, host, **values):
        allowed = {"software", "adapter", "title", "state", "checked", "next_probe", "next_feed", "next_peers",
                   "peer_offset", "older_cursor", "older_done", "last_feed", "last_peers", "last_older", "failures",
                   "error", "muted", "pinned", "post_count", "cooldown", "next_older", "newest_cursor", "feed_scope", "remote_denied"}
        if not values or not values.keys() <= allowed:
            raise ValueError("Unknown server field")
        with self.lock, self.db:
            self.db.execute("UPDATE servers SET " + ",".join(f"{k}=?" for k in values) + " WHERE host=?", (*values.values(), host))

    def due(self, kind, active=(), now=None):
        rows=self.due_many(kind,active,now,limit=1)
        return rows[0] if rows else None

    def due_many(self, kind, active=(), now=None, limit=64):
        now = time.time() if now is None else now
        if kind == "probe":
            condition = "state IN ('queued','error','locked','unsupported') AND next_probe<=?"
            order = "first_seen"
        elif kind == "peers":
            condition = "adapter!='' AND next_peers<=?"
            order = "last_peers"
        elif kind == "older":
            condition = "state='ready' AND older_done=0 AND older_cursor!='' AND next_older<=?"
            order = "last_older"
        else:
            condition = "state='ready' AND next_feed<=?"
            order = "last_feed"
        excluded = " AND host NOT IN (" + ",".join("?" for _ in active) + ")" if active else ""
        with self.lock:
            r = self.db.execute(f"SELECT * FROM servers WHERE muted=0 AND cooldown<=? AND {condition}{excluded} ORDER BY pinned DESC, {order} ASC LIMIT ?", (now, now, *active,limit)).fetchall()
            return [dict(row) for row in r]

    def insert_posts(self, posts):
        count = 0
        with self.lock, self.db:
            for p in posts:
                count += self.db.execute("INSERT OR IGNORE INTO posts(uri,server,author,text,language,media,sensitive,payload,created) VALUES (?,?,?,?,?,?,?,?,?)",
                                         (p.uri, p.server, p.handle, p.text, p.language, bool(p.media), bool(p.sensitive or p.warning), json.dumps(p.payload(), ensure_ascii=False), p.created)).rowcount
        return count

    def page(self, before=None, limit=40, query="", language="", media_only=False, saved_only=False,
             server="", hide_sensitive=False, muted_words="", random_order=False, unseen_only=False):
        clauses = ["NOT EXISTS (SELECT 1 FROM servers s WHERE s.host=p.server AND s.muted=1)",
                   "NOT EXISTS (SELECT 1 FROM muted_authors a WHERE a.handle=p.author)"]
        args = []
        if unseen_only:
            clauses.append("NOT EXISTS (SELECT 1 FROM reader_seen r WHERE r.uri=p.uri)")
        if before is not None:
            clauses.append("(p.created,p.seq)<(?,?)")
            args.extend(before)
        if language == "__unknown__":
            clauses.append("p.language=''")
            language = ""
        for condition, val in [("p.server=?", server), ("p.language=?", language)]:
            if val is not None and val != "":
                clauses.append(condition)
                args.append(val)
        if query:
            clauses.append("(instr(lower(p.text),lower(?))>0 OR instr(lower(p.author),lower(?))>0)")
            args.extend([query, query])
        if saved_only:
            clauses.append("p.saved=1")
        if media_only:
            clauses.append("p.media=1")
        if hide_sensitive:
            clauses.append("p.sensitive=0")
        for word in [w.strip() for w in muted_words.split(",") if w.strip()][:50]:
            clauses.append("instr(lower(p.text),lower(?))=0")
            args.append(word)
        with self.lock:
            order = "RANDOM()" if random_order else "p.created DESC,p.seq DESC"
            rows = self.db.execute("SELECT p.seq,p.saved,p.payload FROM posts p WHERE " + " AND ".join(clauses) + " ORDER BY " + order + " LIMIT ?", (*args, min(limit, 100))).fetchall()
            return [Post(**json.loads(r["payload"]), seq=r["seq"], saved=bool(r["saved"])) for r in rows]

    def reset_reader(self):
        with self.lock,self.db:self.db.execute("DELETE FROM reader_seen")

    def queue_reader(self,posts):
        with self.lock,self.db:
            self.db.executemany("INSERT OR IGNORE INTO reader_seen VALUES (?)",((p.uri,) for p in posts))

    def unread_count(self):
        with self.lock:
            return self.db.execute("""SELECT COUNT(*) FROM posts p
                WHERE NOT EXISTS (SELECT 1 FROM reader_seen r WHERE r.uri=p.uri)
                AND NOT EXISTS (SELECT 1 FROM servers s WHERE s.host=p.server AND s.muted=1)
                AND NOT EXISTS (SELECT 1 FROM muted_authors a WHERE a.handle=p.author)""").fetchone()[0]

    def save_post(self, seq, saved):
        with self.lock, self.db:
            self.db.execute("UPDATE posts SET saved=? WHERE seq=?", (bool(saved), seq))

    def stamp_world(self, host):
        with self.lock, self.db:
            return self.db.execute("INSERT OR IGNORE INTO passport_worlds VALUES (?,?)", (host,time.time())).rowcount > 0

    def stamp_keep(self, uri):
        key=hashlib.sha256(uri.encode("utf-8")).hexdigest()
        with self.lock, self.db:
            return self.db.execute("INSERT OR IGNORE INTO passport_keeps VALUES (?,?)", (key,time.time())).rowcount > 0

    def passport(self):
        from .passport import progress
        with self.lock:
            worlds=self.db.execute("SELECT COUNT(*) FROM passport_worlds").fetchone()[0]
            keeps=self.db.execute("SELECT COUNT(*) FROM passport_keeps").fetchone()[0]
            themes=len(set(self.setting("tried_themes", ["bub"])))
            return progress(worlds, keeps, themes)

    def mute_author(self, handle):
        with self.lock, self.db:
            self.db.execute("INSERT OR IGNORE INTO muted_authors VALUES (?)", (handle,))

    def muted_authors(self):
        with self.lock:
            return [r[0] for r in self.db.execute("SELECT handle FROM muted_authors ORDER BY handle")]

    def unmute_author(self, handle):
        with self.lock, self.db:
            self.db.execute("DELETE FROM muted_authors WHERE handle=?", (handle,))

    def servers(self, query="", limit=1000):
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM servers WHERE instr(host,lower(?))>0 ORDER BY pinned DESC, muted ASC, CASE state WHEN 'ready' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END, host LIMIT ?", (query, limit))]

    def all_hosts(self):
        with self.lock:
            return [r[0] for r in self.db.execute("SELECT host FROM servers ORDER BY host")]

    def stats(self):
        with self.lock:
            states = {r[0]: r[1] for r in self.db.execute("SELECT state, COUNT(*) FROM servers GROUP BY state")}
            posts = self.db.execute("SELECT COUNT(*), COALESCE(SUM(saved),0), COALESCE(MAX(seq),0) FROM posts").fetchone()
            muted = self.db.execute("SELECT COUNT(*) FROM servers WHERE muted=1").fetchone()[0]
            return {"servers": sum(states.values()), "ready": states.get("ready", 0), "queued": states.get("queued", 0),
                    "locked": states.get("locked", 0), "posts": posts[0], "saved": posts[1], "latest": posts[2], "muted": muted}

    def prune(self, keep=50_000):
        with self.lock, self.db:
            self.db.execute("DELETE FROM posts WHERE saved=0 AND seq NOT IN (SELECT seq FROM posts WHERE saved=0 ORDER BY seq DESC LIMIT ?)", (keep,))
            self.db.execute("DELETE FROM reader_seen WHERE NOT EXISTS (SELECT 1 FROM posts p WHERE p.uri=reader_seen.uri)")
            # SQLite reuses freed pages; never vacuum while the UI is scrolling.
