"""Explicitly synthetic, offline fixtures for UI preview and testing."""
import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen, QPixmap

from .content import Post


def populate(store):
    store.add_servers(["garden.example", "makers.example", "night.example"], "offline demo")
    for host, software in (("garden.example", "mastodon"), ("makers.example", "akkoma"), ("night.example", "sharkey")):
        store.update_server(host, software=software, adapter="misskey" if software == "sharkey" else "mastodon", state="ready", checked=time.time(), post_count=40)
    examples = [
        ("Mira", "garden.example", "The internet is still full of little places where people make things just because they can.\n\nFind those places. Stay a while.", False),
        ("Signal Garden", "makers.example", "An experiment in light, repetition, and getting pleasantly lost.\n\n#creativecoding #generativeart", True),
        ("Jun", "night.example", "Made a tiny synthesizer today. One oscillator, one filter, and an unreasonable amount of joy. 🎛", False),
        ("Field Notes", "garden.example", "A walk with no headphones. A notebook with no plan. The good kind of offline.", True),
        ("Ada", "makers.example", "Small tools. Open protocols. Software you can actually take apart.\n\nThis is the corner of the internet I was looking for.", False),
        ("Night Radio", "night.example", "Late-night listening notes. Open the warning when you want to read them.", False),
    ]
    posts = []
    for i in range(120, 0, -1):
        name, host, text, media = examples[(i-1) % len(examples)]
        posts.append(Post(uri=f"https://{host}/posts/{i}", remote_id=str(i), server=host, author=name,
                          handle=name.lower().replace(" ", "") + "@" + host, text=text,
                          url=f"https://{host}/posts/{i}", created=time.time() - i * 72,
                          language="en", warning="Listening notes" if (i-1) % 6 == 5 else "",
                          media=[{"preview": f"https://art.example/{(i-1)%6}", "url": "", "type": "image", "alt": "Synthetic offline demo artwork"}] if media else [],
                          replies=i % 8, boosts=i % 13, likes=i % 29, saved=False))
    store.insert_posts(posts)


def artwork(images):
    for n in (1, 3):
        pix = QPixmap(900, 420)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        gradient = QLinearGradient(0, 0, 900, 420)
        gradient.setColorAt(0, QColor("#133c48" if n == 1 else "#333d26"))
        gradient.setColorAt(1, QColor("#5b3191" if n == 1 else "#123a44"))
        painter.fillRect(pix.rect(), gradient)
        for i in range(23):
            painter.setPen(QPen(QColor("#98eed2" if n == 1 else "#d0ed8a"), 2))
            painter.drawEllipse(450-i*17, 210-i*10, i*34, i*20)
        painter.setPen(QColor("#d4eee9"))
        painter.drawText(32, 35, "SIGNAL STUDY / OFFLINE DEMO")
        painter.end()
        images.cache[f"https://art.example/{n}"] = pix
