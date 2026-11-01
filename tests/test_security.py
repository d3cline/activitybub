"""Synthetic hostile inputs and real worker-policy enforcement, without exploits."""
import io
import json
import os
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
import struct
import subprocess
import sys
import threading
import time
import zlib

import pytest
from PIL import Image, PngImagePlugin
from PySide6.QtCore import QUrl
from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import QApplication

from activity_bub import image_io
from activity_bub.content import rich_text, web_url
from activity_bub.image_policy import MAX_INPUT, supported
from activity_bub.network import FetchError, Network, bounded_json, public_addresses
from activity_bub.store import Store
from activity_bub.ui import PostTextBrowser, safe_tooltip
from tests.test_core import post


requires_worker=pytest.mark.skipif(not supported(),reason="OS-restricted image workers currently require Linux")


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@requires_worker
@pytest.mark.parametrize("fmt",["PNG","JPEG","GIF","WEBP","BMP"])
def test_allowlisted_images_decode_through_restricted_worker(app,fmt):
    stream=io.BytesIO();Image.new("RGB",(80,120),(40,160,90)).save(stream,format=fmt)
    result=image_io.decode_raster(stream.getvalue())
    assert (result.width(),result.height())==(80,120)


@requires_worker
def test_metadata_and_trailing_container_payload_do_not_reach_gui(app):
    stream=io.BytesIO();metadata=PngImagePlugin.PngInfo()
    metadata.add_text("Comment","PRIVATE METADATA MARKER")
    Image.new("RGBA",(40,60),(30,100,200,128)).save(stream,format="PNG",pnginfo=metadata,icc_profile=b"not a usable profile")
    original=stream.getvalue()+b"TRAILING CONTAINER MARKER"
    result=image_io.decode_raster(original)
    assert not result.isNull() and not result.textKeys()
    assert not result.colorSpace().isValid()
    raw=result.constBits().tobytes()
    assert b"PRIVATE METADATA" not in raw and b"TRAILING CONTAINER" not in raw
    assert result.pixelColor(0,0).alpha()==128


@requires_worker
def test_jpeg_orientation_survives_pixel_only_transport(app):
    stream=io.BytesIO();exif=Image.Exif();exif[274]=6
    Image.new("RGB",(80,40),"green").save(stream,format="JPEG",exif=exif)
    result=image_io.decode_raster(stream.getvalue())
    assert (result.width(),result.height())==(40,80)


@requires_worker
def test_huge_png_dimensions_are_rejected_without_allocating_image(app):
    def chunk(kind,data):return struct.pack("!I",len(data))+kind+data+struct.pack("!I",zlib.crc32(kind+data)&0xffffffff)
    payload=b"\x89PNG\r\n\x1a\n"+chunk(b"IHDR",struct.pack("!IIBBBBB",50000,50000,8,2,0,0,0))+chunk(b"IEND",b"")
    assert image_io.decode_raster(payload).isNull()


def test_unsupported_oversized_and_non_raster_inputs_never_start_decoder(app,monkeypatch):
    def reject(*_,**__):raise AssertionError("Unsafe input reached decoder")
    monkeypatch.setattr(image_io,"_exchange",reject)
    for payload in (b"<svg onload='anything'>",b"<html><script>anything</script>",b"%PDF-1.7",b"II*\x00",b"MZ",b"\x89PNG\r\n\x1a\n"+b"x"*MAX_INPUT):
        assert image_io.decode_raster(payload).isNull()
    monkeypatch.setattr(image_io,"supported",lambda:False)
    assert image_io.decode_raster(b"GIF89a").isNull()


@pytest.mark.parametrize("packet",[
    None,b"",struct.pack("!4sII",b"EXEC",1,1)+b"x"*4,
    struct.pack("!4sII",b"BUB1",0,1),struct.pack("!4sII",b"BUB1",0xffffffff,1),
    struct.pack("!4sII",b"BUB1",1,1)+b"x"*3,
    struct.pack("!4sII",b"BUB1",1,1)+b"x"*4+b"trailer",
])
def test_worker_response_is_validated_before_raw_image_allocation(app,packet):
    assert image_io.packet_image(packet).isNull()


@requires_worker
def test_worker_cannot_read_write_network_fork_or_exec(tmp_path):
    canary=tmp_path/"canary.txt";canary.write_text("leave this alone")
    code='''
import ctypes,json,os,resource,socket,sys
from activity_bub.image_policy import restrict_worker
path=sys.argv[1]
libc=ctypes.CDLL(None,use_errno=True)
number=302 if os.uname().machine=="x86_64" else 261
requested=(ctypes.c_uint64*2)(100,100)
def change_limits():
    if libc.syscall(number,0,resource.RLIMIT_CPU,ctypes.byref(requested),0)==-1:
        raise OSError(ctypes.get_errno(),"limit change denied")
limits=restrict_worker()
blocked=[]
for name,action in [("read",lambda:open(path).read()),("write",lambda:open(path,"w")),("network",lambda:socket.socket()),("exec",lambda:os.execv("/bin/true",["/bin/true"])),("fork",os.fork),("change-limits",change_limits)]:
    try:action()
    except OSError as e:
        if e.errno==1:blocked.append(name)
os.write(1,json.dumps({"blocked":blocked,"limits":limits}).encode())
os._exit(0)
'''
    result=subprocess.run([sys.executable,"-c",code,str(canary)],capture_output=True,timeout=5,check=True)
    result=json.loads(result.stdout)
    assert result["blocked"]==["read","write","network","exec","fork","change-limits"]
    assert result["limits"]["cpu"]==[3,3]
    assert result["limits"]["memory"]==[512*1024**2,512*1024**2]
    assert result["limits"]["core"]==[0,0]
    assert canary.read_text()=="leave this alone"


@requires_worker
def test_hung_and_flooding_workers_are_stopped_and_output_is_bounded():
    started=time.monotonic()
    assert image_io._exchange([sys.executable,"-c","import time;time.sleep(20)"],b"",seconds=.15) is None
    assert time.monotonic()-started<2
    flood="import os\nwhile True: os.write(1,b'x'*65536)"
    assert image_io._exchange([sys.executable,"-c",flood],b"",seconds=2,max_output=1024) is None
    stop=threading.Event();stop.set()
    assert image_io._exchange([sys.executable,"-c","import time;time.sleep(20)"],b"",stop=stop) is None


def test_worker_environment_does_not_inherit_secrets_or_python_injection(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY","synthetic-test-value")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY","synthetic-test-value")
    monkeypatch.setenv("PYTHONPATH","/untrusted/synthetic/path")
    monkeypatch.setenv("LD_PRELOAD","/untrusted/synthetic/preload")
    environment=image_io.worker_environment()
    assert not {"OPENAI_API_KEY","AWS_SECRET_ACCESS_KEY","PYTHONPATH","LD_PRELOAD"}&environment.keys()


def test_frozen_worker_relaunches_app_with_its_internal_flag(monkeypatch):
    monkeypatch.setattr(sys,"frozen",True,raising=False)
    command,_=image_io.worker_command()
    assert command==[sys.executable,"--_decode-image"]


@pytest.mark.parametrize("url",[
    "file:///etc/passwd","javascript:alert(1)","data:image/svg+xml,anything",
    "https://127.0.0.1/","https://127.1/","https://0x7f.0.0.1/","https://one.local/",
    "https://one.example\\anything/","https://one.example/\x00bad","https://one.example/\r\nbad",
    "https://user:secret@one.example/","https://one.example:8443/","https://one.example/"+"x"*4100,
])
def test_all_openable_and_preview_urls_reject_unsafe_schemes_and_ambiguities(url):
    assert web_url(url)==""


@pytest.mark.parametrize("address",["64:ff9b::c0a8:101","2002:c0a8:0101::"])
def test_ipv6_translation_does_not_bypass_private_network_guard(monkeypatch,address):
    import socket
    monkeypatch.setattr(socket,"getaddrinfo",lambda *a,**kw:[(socket.AF_INET6,socket.SOCK_STREAM,6,"",(address,443,0,0))])
    with pytest.raises(ValueError,match="non-public"):public_addresses("one.example")


def test_post_reader_has_no_document_or_resource_loading_path(app,tmp_path):
    local=tmp_path/"private.html";local.write_text("SYNTHETIC PRIVATE DOCUMENT")
    reader=PostTextBrowser()
    url=QUrl.fromLocalFile(str(local))
    reader.setSource(url)
    assert reader.source().isEmpty() and "PRIVATE" not in reader.toPlainText()
    assert reader.loadResource(QTextDocument.ResourceType.ImageResource,url) is None
    assert not reader.acceptDrops() and not reader.acceptRichText()
    reader.setHtml(rich_text('<img src="file:///private.png"><script>bad()</script>'))
    assert '<img src="file:///private.png">' in reader.toPlainText()
    assert "<img" not in safe_tooltip('<img src="file:///private.png">')
    reader.close()


@pytest.mark.parametrize("payload",[b"["*1500+b"0"+b"]"*1500,b'{"value":NaN}',b'{"value":Infinity}'])
def test_non_finite_or_excessively_nested_json_fails_cleanly(monkeypatch,payload):
    network=Network();monkeypatch.setattr(network,"request",lambda *_,**__:payload)
    with pytest.raises(FetchError,match="valid JSON"):network.json("https://one.example/")


def test_nesting_guard_understands_quoted_brackets_and_escaped_quotes():
    value={"text":"["*2000+'\\"}]]]]',"good":[{"n":3}]}
    assert bounded_json(json.dumps(value).encode())==value


@pytest.mark.parametrize("encoding",["utf-16","utf-16-le","utf-16-be","utf-32"])
def test_json_encoding_cannot_disagree_with_depth_scanner(encoding):
    with pytest.raises((ValueError,UnicodeError)):
        bounded_json('{"fine":1}'.encode(encoding))


def test_post_and_search_inputs_remain_sql_data():
    store=Store(":memory:")
    attack="'); DROP TABLE posts;--"
    p=post();p.text=attack;store.insert_posts([p])
    assert store.page(query=attack)[0].text==attack
    assert store.stats()["posts"]==1
    store.close()
