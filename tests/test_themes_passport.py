import os
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from activity_bub.passport import progress
from activity_bub.store import Store
from activity_bub.themes import THEMES,get_theme,stylesheet
from activity_bub.ui import Window
from tests.test_feed_images import make_post


def luminance(color):
    rgb=[int(color[i:i+2],16)/255 for i in (1,3,5)]
    linear=[v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4 for v in rgb]
    return sum(a*b for a,b in zip(linear,(.2126,.7152,.0722)))


def contrast(a,b):
    x,y=sorted((luminance(a),luminance(b)))
    return (y+.05)/(x+.05)


@pytest.mark.parametrize("theme",THEMES,ids=lambda t:t.key)
def test_palettes_have_readable_body_and_action_colors(theme):
    assert contrast(theme.text,theme.card)>=4.5
    assert contrast(theme.muted,theme.card)>=4.5
    assert contrast(theme.accent_text,theme.accent)>=4.5
    assert theme.bg in stylesheet(theme) and theme.accent in stylesheet(theme)
    assert not stylesheet(theme).startswith("+")


def test_passport_stamps_are_unique_persistent_and_survive_cache_cleanup(tmp_path):
    path=tmp_path/"cache.sqlite3"
    s=Store(path)
    assert s.stamp_world("one.example") and not s.stamp_world("one.example")
    assert s.stamp_keep("https://one.example/p/1")
    assert not s.stamp_keep("https://one.example/p/1")
    s.insert_posts([make_post(1)]);s.prune(0);s.close()
    s=Store(path);result=s.passport()
    assert result["xp"]==35 and result["worlds"]==1 and result["keeps"]==1
    assert {b["key"] for b in result["badges"] if b["earned"]}=={"contact","treasure"}
    s.close()


def test_ranks_and_palette_badges_have_clear_thresholds():
    assert progress(1,0)["level"]==1
    assert progress(2,0)["level"]==2
    assert progress(100,0)["rank"]=="Void Cartographer"
    assert next(b for b in progress(0,0,3)["badges"] if b["key"]=="palette")["earned"]
    assert get_theme("missing").key=="bub"


def test_surprise_queries_still_respect_mutes_and_filters():
    s=Store(":memory:")
    a,b=make_post(1,True),make_post(2)
    b.server="two.example";b.handle="bub@two.example"
    a.language="en";b.language="ja"
    s.add_servers(["one.example","two.example"]);s.insert_posts([a,b])
    for _ in range(10):
        assert s.page(limit=1,random_order=True,media_only=True)[0].uri==a.uri
    s.update_server("one.example",muted=1)
    assert not s.page(limit=1,random_order=True,media_only=True)
    assert s.page(limit=1,random_order=True,language="ja")[0].uri==b.uri
    s.close()


def test_live_theme_changes_persist_and_rewards_can_be_disabled(tmp_path):
    app=QApplication.instance() or QApplication([])
    s=Store(tmp_path/"cache.sqlite3");s.insert_posts([make_post(1)])
    w=Window(s,tmp_path,demo=True)
    w.apply_theme("daylight")
    assert w.delegate.theme.key=="daylight" and s.setting("theme")=="daylight"
    assert w.theme_actions["daylight"].isChecked()
    p=w.model.posts[0]
    w.open_post(p);w.reader_dialog.reject()
    w.save_post(p);w.save_post(p);w.save_post(p)
    assert s.passport()["xp"]==35
    s.set_setting("rewards",False)
    w.open_post(make_post(2));w.reader_dialog.reject()
    w.apply_theme("arcade")
    assert s.passport()["xp"]==35
    assert "arcade" not in s.setting("tried_themes")
    w.close();app.processEvents()
    s=Store(tmp_path/"cache.sqlite3")
    w=Window(s,tmp_path,demo=True)
    assert w.theme.key=="arcade"
    w.close();app.processEvents()
