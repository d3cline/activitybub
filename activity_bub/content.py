from __future__ import annotations

import html
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlsplit


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "iframe", "object"):
            self.hidden += 1
        elif not self.hidden and tag in ("br", "p", "div", "li", "blockquote"):
            self.parts.append("\n")
        elif not self.hidden and tag == "img":
            self.parts.append(dict(attrs).get("alt", ""))

    def handle_endtag(self, tag):
        if tag in ("script", "style", "iframe", "object"):
            self.hidden = max(0, self.hidden - 1)
        elif not self.hidden and tag in ("p", "div", "li", "blockquote"):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain(value) -> str:
    parser = PlainText()
    parser.feed(str(value or "")[:100_000])
    return re.sub(r"\n{3,}", "\n\n", "".join(parser.parts)).strip()[:30_000]


def web_url(value) -> str:
    value = str(value or "").strip()
    try:
        u = urlsplit(value)
        if u.scheme == "https" and u.hostname and not u.username and not u.password and u.port in (None, 443):
            return value[:4096]
    except ValueError:
        pass
    return ""


def rich_text(text: str) -> str:
    # Build our own tiny HTML subset; remote post HTML never enters Qt's renderer.
    pieces = []
    for part in re.split(r"(https://[^\s<>]+)", text):
        url = web_url(part.rstrip(".,);!?")) if part.startswith("https://") else ""
        if url:
            pieces.append(f'<a href="{html.escape(url, quote=True)}">{html.escape(url)}</a>' + html.escape(part[len(url):]))
        else:
            pieces.append(html.escape(part))
    return "".join(pieces).replace("\n", "<br>")


def timestamp(value) -> float:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (ValueError, TypeError, OverflowError):
        return 0


@dataclass
class Post:
    uri: str
    remote_id: str
    server: str
    author: str
    handle: str
    text: str
    url: str
    created: float = 0
    language: str = ""
    warning: str = ""
    sensitive: bool = False
    media: list[dict] = field(default_factory=list)
    replies: int = 0
    boosts: int = 0
    likes: int = 0
    seq: int = 0
    saved: bool = False

    def payload(self):
        d = asdict(self)
        d.pop("seq")
        d.pop("saved")
        return d


def mastodon_post(raw: dict, host: str) -> Post | None:
    if not isinstance(raw, dict):
        return None
    raw = raw.get("reblog") or raw
    if raw.get("visibility") != "public" or not raw.get("id"):
        return None
    a = raw.get("account") or {}
    acct = str(a.get("acct") or a.get("username") or "unknown")[:300]
    handle = acct if "@" in acct else f"{acct}@{host}"
    url = web_url(raw.get("url")) or web_url(raw.get("uri"))
    if not url:
        return None
    media = []
    for m in (raw.get("media_attachments") or [])[:8]:
        if isinstance(m, dict):
            media.append({"url": web_url(m.get("url")), "preview": web_url(m.get("preview_url")),
                          "type": str(m.get("type", "unknown"))[:40], "alt": plain(m.get("description"))[:1000]})
    return Post(uri=web_url(raw.get("uri")) or url, remote_id=str(raw["id"])[:128], server=host,
                author=plain(a.get("display_name") or a.get("username") or "Unknown")[:200],
                handle=handle, text=plain(raw.get("content")), url=url, created=timestamp(raw.get("created_at")),
                language=str(raw.get("language") or "")[:20], warning=plain(raw.get("spoiler_text"))[:1000],
                sensitive=bool(raw.get("sensitive")), media=media,
                replies=int(raw.get("replies_count") or 0), boosts=int(raw.get("reblogs_count") or 0),
                likes=int(raw.get("favourites_count") or 0))


def misskey_post(raw: dict, host: str) -> Post | None:
    if not isinstance(raw, dict):
        return None
    if raw.get("renote") and not raw.get("text"):
        raw = raw["renote"]
    if raw.get("visibility") != "public" or raw.get("localOnly") or not raw.get("id"):
        return None
    a = raw.get("user") or {}
    handle = f"{str(a.get('username') or 'unknown')[:200]}@{str(a.get('host') or host)[:253]}"
    remote_id = str(raw["id"])[:128]
    # Only use server-generated IDs in fallback URLs after restricting their alphabet.
    if not re.fullmatch(r"[A-Za-z0-9_-]+", remote_id):
        return None
    url = web_url(raw.get("url")) or web_url(raw.get("uri")) or f"https://{host}/notes/{remote_id}"
    media = []
    for m in (raw.get("files") or [])[:8]:
        if isinstance(m, dict):
            media.append({"url": web_url(m.get("url")), "preview": web_url(m.get("thumbnailUrl")),
                          "type": str(m.get("type", "unknown"))[:80], "alt": str(m.get("comment") or "")[:1000]})
    sensitive = any(bool(m.get("isSensitive")) for m in raw.get("files", []) if isinstance(m, dict))
    return Post(uri=web_url(raw.get("uri")) or url, remote_id=remote_id, server=host,
                author=str(a.get("name") or a.get("username") or "Unknown")[:200], handle=handle,
                text=str(raw.get("text") or "")[:30_000], url=url, created=timestamp(raw.get("createdAt")),
                warning=str(raw.get("cw") or "")[:1000], sensitive=sensitive, media=media,
                replies=int(raw.get("repliesCount") or 0), boosts=int(raw.get("renoteCount") or 0),
                likes=sum(int(n) for n in (raw.get("reactions") or {}).values()))
