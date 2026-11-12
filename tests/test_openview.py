"""
Pruebas de OpenView.

    python -m unittest discover -s tests -v          (todas, necesita Internet)
    set OPENVIEW_OFFLINE=1 & python -m unittest discover -s tests -v   (solo las que no usan red)

No tocan tu configuración real: usan una carpeta temporal como APPDATA.
"""
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# Configuración aislada ANTES de importar app
TMP = tempfile.mkdtemp(prefix="openview-test-")
os.environ["APPDATA"] = os.path.join(TMP, "appdata")

import numpy as np  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402
import openview  # noqa: E402
import hdri  # noqa: E402
import extras  # noqa: E402
import app  # noqa: E402
import math  # noqa: E402

OFFLINE = os.environ.get("OPENVIEW_OFFLINE") == "1"
net = unittest.skipIf(OFFLINE, "sin red (OPENVIEW_OFFLINE=1)")

OFFICIAL_ID = "OhR7Uw9BShEzj2LCYE3y-g"          # Puerta del Sol, Madrid
USER360_ID = "CIHM0ogKEICAgIDhx_qQOg"       # Torre Eiffel
OFFICIAL_URL = ("https://www.google.com/maps/@40.4168,-3.7038,3a,75y,90h,90t/data=!3m6!1e1!3m4!1s"
                + OFFICIAL_ID + "!2e0!7i16384!8i8192")
USER360_URL = ("https://www.google.com/maps/@48.8584,2.2945,3a,75y,90h,90t/data=!3m7!1e1!3m5!1s"
                   + USER360_ID + "!2e10!6shttps:%2F%2Flh3.ggpht.com!7i5660!8i2830")


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


# ============================================================ sin red
class TestParseLink(unittest.TestCase):
    def test_official_url(self):
        self.assertEqual(openview.parse_link(OFFICIAL_URL), (OFFICIAL_ID, 40.4168, -3.7038))

    def test_user360_url(self):
        pid, lat, lon = openview.parse_link(USER360_URL)
        self.assertEqual(pid, USER360_ID)
        self.assertTrue(openview.is_third_party(pid))

    def test_place_url_with_feature_id(self):
        url = ("https://www.google.com/maps/place/Puerta+del+Sol/@40.4168,-3.7038,3a,75y,90t/"
               "data=!3m8!1e1!3m6!1s" + OFFICIAL_ID + "!2e0!5s20250501T000000!7i16384!8i8192"
               "!4m6!3m5!1s0xd42287e1d9b7d67:0x8f2d1c7c1f3b0b0a!8m2!3d40.41!4d-3.70")
        self.assertEqual(openview.parse_link(url)[0], OFFICIAL_ID)

    def test_quotes_and_spaces(self):
        self.assertEqual(openview.parse_link(f'  "{OFFICIAL_URL}"  ')[0], OFFICIAL_ID)

    def test_without_scheme(self):
        self.assertEqual(openview.parse_link(OFFICIAL_URL.replace("https://", ""))[0], OFFICIAL_ID)

    def test_raw_id(self):
        self.assertEqual(openview.parse_link(OFFICIAL_ID), (OFFICIAL_ID, None, None))

    def test_coordinates_only(self):
        self.assertEqual(openview.parse_link("https://www.google.com/maps/@40.4168,-3.7038,3a,75y,90t"),
                         (None, 40.4168, -3.7038))

    def test_api_style_url(self):
        url = "https://www.google.com/maps/@?api=1&map_action=pano&pano=" + OFFICIAL_ID
        self.assertEqual(openview.parse_link(url)[0], OFFICIAL_ID)

    def test_viewpoint_url(self):
        url = "https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=40.4168,-3.7038"
        self.assertEqual(openview.parse_link(url), (None, 40.4168, -3.7038))

    def test_consent_redirect(self):
        from urllib.parse import quote
        url = "https://consent.google.com/m?continue=" + quote(OFFICIAL_URL, safe="")
        self.assertEqual(openview.parse_link(url)[0], OFFICIAL_ID)

    def test_empty(self):
        with self.assertRaises(openview.OpenViewError):
            openview.parse_link("   ")

    def test_link_without_panorama(self):
        with self.assertRaises(openview.OpenViewError):
            openview.parse_link("https://www.google.com/maps/search/restaurantes")


class TestHelpers(unittest.TestCase):
    def test_third_party(self):
        self.assertFalse(openview.is_third_party(OFFICIAL_ID))
        self.assertTrue(openview.is_third_party(USER360_ID))
        self.assertTrue(openview.is_third_party("AF1QipN" + "x" * 30))

    def test_protobuf_url(self):
        self.assertEqual(openview._pb_msg({1: {1: "a", 2: 5}, 2: openview.Enum_(3), 3: True})[1],
                         "!1m2!1sa!2i5!2e3!3b1")

    def test_safe_name(self):
        self.assertEqual(openview.safe("a/b:c*d"), "a_b_c_d")

    def test_roll_to_north(self):
        # 360 columnas, la columna x vale x. El centro (180) mira al rumbo 90,
        # así que el norte está en la columna 90 y debe acabar en el centro.
        img = Image.new("L", (360, 2))
        img.putdata([x for _ in range(2) for x in range(360)])
        out = openview.roll_to_north(img, 90)
        self.assertEqual(out.getpixel((180, 0)), 90)
        self.assertEqual(out.size, img.size)
        self.assertIs(openview.roll_to_north(img, 0), img)


def fake_meta(third_party=False, w=1024, h=512):
    return {"id": USER360_ID if third_party else OFFICIAL_ID, "sizes": [(w // 2, h // 2), (w, h)],
            "tile": (512, 512), "lat": 40.0, "lon": -3.0, "heading": 45.0, "pitch": 90,
            "roll": 0, "date": "2025-05", "uploader": None, "address": "Calle", "third_party": third_party}


class TestProcessOffline(unittest.TestCase):
    def setUp(self):
        self.out = tempfile.mkdtemp(dir=TMP)

    def run_process(self, **kw):
        img = Image.new("RGB", (1024, 512), (10, 120, 200))
        with mock.patch.object(openview, "download_official", return_value=(img, None)):
            return openview.process("x", self.out, meta=fake_meta(), log=lambda *_: None, **kw)

    def test_formats(self):
        for fmt in ("jpg", "png", "tif"):
            path = self.run_process(fmt=fmt)
            self.assertTrue(path.endswith("." + fmt), path)
            self.assertEqual(Image.open(path).size, (1024, 512))

    def test_json_optional(self):
        path = self.run_process()
        self.assertFalse(os.path.exists(os.path.splitext(path)[0] + ".json"))
        path = self.run_process(save_json=True)
        with open(os.path.splitext(path)[0] + ".json", encoding="utf-8") as f:
            self.assertEqual(json.load(f)["id"], OFFICIAL_ID)

    def test_creates_missing_folder(self):
        self.out = os.path.join(self.out, "nueva", "sub")
        self.assertTrue(os.path.exists(self.run_process()))

    def test_north_and_plain_do_not_overwrite_each_other(self):
        a = self.run_process()
        b = self.run_process(north=True)
        self.assertNotEqual(a, b, "la versión alineada al norte sobrescribe la normal")

    def test_third_party_non_jpeg_bytes_are_reencoded(self):
        img = Image.new("RGB", (64, 32))
        buf = io.BytesIO()
        img.save(buf, "WEBP")
        with mock.patch.object(openview, "download_third_party", return_value=(img, buf.getvalue())):
            path = openview.process("x", self.out, meta=fake_meta(True, 64, 32), log=lambda *_: None)
        self.assertEqual(Image.open(path).format, "JPEG", "guarda bytes no-JPEG con extensión .jpg")


# ============================================================ con red
@net
class TestLive(unittest.TestCase):
    def test_official_metadata(self):
        m = openview.get_metadata_by_id(OFFICIAL_ID)
        self.assertEqual(m["sizes"][-1], (16384, 8192))
        self.assertEqual(m["tile"], (512, 512))
        self.assertIsNotNone(m["heading"])

    def test_user360_metadata(self):
        m = openview.get_metadata_by_id(USER360_ID)
        self.assertEqual(m["sizes"][-1], (5660, 2830))
        self.assertTrue(m["uploader"])

    def test_invalid_id_gives_clear_error(self):
        with self.assertRaises(openview.OpenViewError) as cm:
            openview.get_metadata_by_id("AAAAAAAAAAAAAAAAAAAAAA")
        self.assertIn("panorama", str(cm.exception))

    def test_nearest(self):
        m = openview.resolve("https://www.google.com/maps/@40.4168,-3.7038,3a,75y,90t", log=lambda *_: None)
        self.assertFalse(m["third_party"])

    def test_nowhere(self):
        with self.assertRaises(openview.OpenViewError):
            openview.resolve("https://www.google.com/maps/@0.0001,-30.0001,3a,75y,90t", log=lambda *_: None)

    def test_thumbnails(self):
        for pid in (OFFICIAL_ID, USER360_ID):
            m = openview.get_metadata_by_id(pid)
            im = Image.open(io.BytesIO(openview.thumbnail(m, 1024)))
            self.assertLessEqual(im.width, 1024)
            self.assertAlmostEqual(im.width / im.height, 2, delta=0.05)

    def test_download_official_small_has_no_black_tiles(self):
        out = tempfile.mkdtemp(dir=TMP)
        path = openview.process(OFFICIAL_URL, out, max_width=2048, log=lambda *_: None)
        im = Image.open(path).convert("L")
        self.assertEqual(im.size, (2048, 1024))
        # cada tesela de 512 debe tener contenido (antes salían huecos negros)
        for x in range(0, 2048, 512):
            for y in range(0, 1024, 512):
                self.assertGreater(max(im.crop((x, y, x + 512, y + 512)).getdata()), 20)

    def test_download_user360_full(self):
        out = tempfile.mkdtemp(dir=TMP)
        path = openview.process(USER360_URL, out, log=lambda *_: None)
        self.assertEqual(Image.open(path).size, (5660, 2830))


# ============================================================ aplicación (servidor local)
class AppServer:
    def __init__(self):
        from http.server import ThreadingHTTPServer
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def call(self, path, body=None, headers=None, origin=None):
        h = {"Content-Type": "application/json", **(headers or {})}
        tok = getattr(app, "TOKEN", None)
        if tok and headers is None:
            h["X-Token"] = tok
        if origin:
            h["Origin"] = origin
        req = urllib.request.Request(self.base + path, headers=h,
                                     data=None if body is None else json.dumps(body).encode())
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
                return r.status, (json.loads(data) if r.headers.get_content_type() == "application/json" else data)
        except urllib.error.HTTPError as e:
            return e.code, None

    def wait(self, pred, timeout=90):
        t = time.time()
        while time.time() - t < timeout:
            st = self.call("/api/state")[1]
            if pred(st):
                return st
            time.sleep(0.3)
        raise AssertionError("tiempo de espera agotado: " + json.dumps(st)[:500])


class TestAppOffline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = AppServer()

    def test_page_and_state(self):
        code, html = self.s.call("/")
        self.assertEqual(code, 200)
        self.assertIn(b"OpenView", html)
        code, st = self.s.call("/api/state")
        self.assertEqual(code, 200)
        self.assertIn("settings", st)

    def test_settings_persist_and_ignore_unknown(self):
        out = os.path.join(TMP, "salida")
        code, s = self.s.call("/api/settings", {"outdir": out, "fmt": "png", "hack": 1})
        self.assertEqual(s["fmt"], "png")
        self.assertNotIn("hack", s)
        with open(app.CONF_FILE, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["outdir"], out)
        self.s.call("/api/settings", {"fmt": "jpg"})

    def test_unknown_route(self):
        self.assertEqual(self.s.call("/api/nada", {})[0], 404)

    def test_bad_json_is_rejected_cleanly(self):
        req = urllib.request.Request(self.s.base + "/api/add", data=b"{no es json",
                                     headers={"X-Token": getattr(app, "TOKEN", "") or ""})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(cm.exception.code, 400)
        self.assertEqual(self.s.call("/api/state")[0], 200)

    def test_wrong_token_rejected(self):
        code, _ = self.s.call("/api/state", headers={"X-Token": "falso"})
        self.assertEqual(code, 403)

    def test_page_carries_token(self):
        html = self.s.call("/")[1]
        self.assertIn(app.TOKEN.encode(), html)
        self.assertNotIn(b"__APP_TOKEN__", html)

    def test_settings_type_checked(self):
        before = dict(app.settings)
        self.s.call("/api/settings", {"fmt": "exe", "north": "si", "outdir": 5})
        self.assertEqual(app.settings, before)

    def test_rejects_requests_from_other_websites(self):
        """Una web cualquiera abierta en el navegador no debe poder mandar órdenes a la app."""
        code, _ = self.s.call("/api/settings", {"outdir": "C:\\malo"}, headers={},
                              origin="https://web-cualquiera.example")
        self.assertIn(code, (401, 403), "la app acepta órdenes de otras webs")
        self.assertNotEqual(app.settings["outdir"], "C:\\malo")


@net
class TestAppLive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = AppServer()
        cls.out = tempfile.mkdtemp(dir=TMP)
        cls.s.call("/api/settings", {"outdir": cls.out, "fmt": "jpg", "north": False, "json": False})

    def test_full_flow(self):
        s = self.s
        s.call("/api/add", {"links": [USER360_URL, "https://www.google.com/maps/search/bares"]})
        st = s.wait(lambda st: all(i["status"] not in ("resolving",) for i in st["items"])
                    and any(i["hasThumb"] for i in st["items"]))
        good = next(i for i in st["items"] if i["status"] == "ready")
        bad = next(i for i in st["items"] if i["status"] == "error")
        self.assertIn("No encuentro", bad["error"])
        self.assertEqual(good["meta"]["width"], 5660)
        code, jpg = s.call(f"/api/thumb/{good['id']}")
        self.assertEqual(code, 200)
        self.assertEqual(jpg[:2], b"\xff\xd8")

        # duplicado
        s.call("/api/add", {"links": [USER360_URL]})
        st = s.wait(lambda st: not any(i["status"] == "resolving" for i in st["items"]))
        self.assertTrue(any("ya está" in (i["error"] or "") for i in st["items"]))

        s.call("/api/download", {"ids": [good["id"]]})
        st = s.wait(lambda st: next(i for i in st["items"] if i["id"] == good["id"])["status"]
                    in ("done", "error"))
        it = next(i for i in st["items"] if i["id"] == good["id"])
        self.assertEqual(it["status"], "done", it["error"])
        self.assertTrue(os.path.exists(it["path"]))
        self.assertEqual(os.listdir(self.out), [os.path.basename(it["path"])])

        s.call("/api/clear-done", {})
        st = s.call("/api/state")[1]
        self.assertFalse(any(i["status"] in ("done", "error") for i in st["items"]))

    def test_two_identical_links_at_once(self):
        s = self.s
        before = len(s.call("/api/state")[1]["items"])
        s.call("/api/add", {"links": [OFFICIAL_URL, OFFICIAL_URL]})
        st = s.wait(lambda st: not any(i["status"] == "resolving" for i in st["items"]))
        ready = [i for i in st["items"] if i["status"] == "ready" and i["meta"]["id"] == OFFICIAL_ID]
        self.assertEqual(len(ready), 1, "el mismo panorama pegado dos veces entra dos veces")
        for i in st["items"][: len(st["items"]) - before]:
            s.call("/api/remove", {"id": i["id"]})

    def test_cancel_full_resolution_download(self):
        s = self.s
        out = tempfile.mkdtemp(dir=TMP)
        s.call("/api/settings", {"outdir": out})
        ids = s.call("/api/add", {"links": [OFFICIAL_ID]})[1]["ids"]
        s.wait(lambda st: any(i["id"] == ids[0] and i["status"] == "ready" for i in st["items"]))
        s.call("/api/download", {"ids": ids})
        s.wait(lambda st: any(i["id"] == ids[0] and i["status"] == "downloading"
                              and i["progress"] > 0.05 for i in st["items"]))
        t = time.time()
        s.call("/api/cancel", {"id": ids[0]})
        st = s.wait(lambda st: any(i["id"] == ids[0] and i["status"] != "downloading"
                                   for i in st["items"]), timeout=30)
        it = next(i for i in st["items"] if i["id"] == ids[0])
        s.call("/api/settings", {"outdir": self.out})
        s.call("/api/remove", {"id": ids[0]})
        self.assertEqual(it["status"], "ready")
        self.assertIn("cancelada", it["notice"])
        self.assertLess(time.time() - t, 15, "la cancelación tarda demasiado")
        self.assertEqual(os.listdir(out), [], "deja archivos a medias")

    def test_unwritable_folder_gives_clear_error(self):
        s = self.s
        blocker = os.path.join(self.out, "soy_un_archivo")
        open(blocker, "w").close()
        s.call("/api/settings", {"outdir": os.path.join(blocker, "sub")})
        ids = s.call("/api/add", {"links": [USER360_ID]})[1]["ids"]
        s.wait(lambda st: any(i["id"] == ids[0] and i["status"] != "resolving" for i in st["items"]))
        s.call("/api/download", {"ids": ids})
        st = s.wait(lambda st: any(i["id"] == ids[0] and i["status"] in ("done", "error")
                                   for i in st["items"]))
        it = next(i for i in st["items"] if i["id"] == ids[0])
        s.call("/api/settings", {"outdir": self.out})
        s.call("/api/remove", {"id": ids[0]})
        self.assertEqual(it["status"], "error")
        self.assertIn("carpeta", it["error"].lower(), "mensaje técnico: " + it["error"])


class TestHistory(unittest.TestCase):
    def test_parse_history_from_metadata(self):
        others = [[[2, "AAA"]], [[2, "BBB"]], [[2, "CCC"]]]
        m5 = [None] * 13
        m5[1] = [[None, None, 40.0, -3.0], None, [10.0, 90.0, 0.0]]
        m5[3] = [others]
        m5[8] = [[1, [2019, 5]], [2, [2008, 8]], [9, [2000, 1]]]  # el 9 no existe: se ignora
        msg = [None, [None, "AAA"], [None, None, None, [[[[256, 512]]], [512, 512]]], None, None,
               [m5], [None] * 7 + [[2025, 5]]]
        meta = openview._parse_pano_msg(msg)
        self.assertEqual(meta["history"], [{"id": "BBB", "date": "2019-05"}, {"id": "CCC", "date": "2008-08"}])

    def test_merge_dates_sorted_and_unique(self):
        meta = {"id": "A", "date": "2025-05", "history": [{"id": "B", "date": "2008-08"},
                                                          {"id": "C", "date": "2019-01"}]}
        old = [{"id": "B", "date": "2008-08"}, {"id": "D", "date": "2021-05"}]
        self.assertEqual([d["id"] for d in app.merge_dates(old, meta)], ["A", "D", "C", "B"])


def synthetic_pano(w=1024, sun=(0.3, 0.2), alpha=False):
    """Cielo azul, suelo gris y un sol quemado en (u, v)."""
    img = Image.new("RGBA" if alpha else "RGB", (w, w // 2), (90, 140, 210, 255))
    d = ImageDraw.Draw(img)
    d.rectangle((0, w // 4, w, w // 2), fill=(110, 105, 100, 255))
    cx, cy, r = sun[0] * w, sun[1] * w / 2, w / 200
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(255, 255, 255, 255))
    return img


class TestHDRI(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(dir=TMP)

    def test_color_roundtrip(self):
        u8 = np.arange(256, dtype=np.uint8).reshape(16, 16)[..., None].repeat(3, 2)
        self.assertTrue(np.array_equal(hdri.linear_to_srgb_u8(hdri.srgb_to_linear(u8)), u8))

    def test_detects_sun_where_it_is(self):
        lin = hdri.srgb_to_linear(np.asarray(synthetic_pano(sun=(0.3, 0.2))))
        u, v, conf = hdri.detect_sun(lin)
        self.assertAlmostEqual(u, 0.3, delta=0.01)
        self.assertAlmostEqual(v, 0.2, delta=0.01)

    def test_no_sun_on_overcast(self):
        img = Image.new("RGB", (1024, 512), (150, 150, 155))
        self.assertIsNone(hdri.detect_sun(hdri.srgb_to_linear(np.asarray(img))))

    def test_reflection_below_horizon_is_not_the_sun(self):
        img = synthetic_pano(sun=(0.3, 0.2))
        d = ImageDraw.Draw(img)
        d.rectangle((700, 400, 760, 430), fill=(255, 255, 255))  # reflejo grande en el suelo
        u, v, _ = hdri.detect_sun(hdri.srgb_to_linear(np.asarray(img)))
        self.assertLess(v, 0.5)

    def test_sun_ratio_is_reached(self):
        lin = hdri.srgb_to_linear(np.asarray(synthetic_pano()))
        p = {**hdri.DEFAULTS, "sun_ratio": 6.0}
        gain, _, _ = hdri.solve_sun_gain(lin, (0.3, 0.2), p)
        out = hdri.process_array(lin, p, (0.3, 0.2), gain)
        base = hdri.process_array(lin, p, None, 1.0)
        # medir de nuevo sobre el resultado: luz que aporta el sol / luz del resto, sobre el suelo
        h, w, _ = lin.shape
        dirs = hdri.row_dirs(0, h, h, w)
        wt = hdri._weights(h)[:, None] * np.clip(dirs[1], 0, None)
        added = ((out - base) @ hdri.LUMA * wt).sum()
        sky = (base @ hdri.LUMA * wt).sum()
        self.assertAlmostEqual(added / sky, 6.0, delta=0.05)

    def test_preview_info(self):
        jpg, info = hdri.preview(synthetic_pano(), {})
        self.assertEqual(jpg[:2], b"\xff\xd8")
        self.assertTrue(info["sun_auto"])
        self.assertGreater(info["sun_gain_ev"], 3)
        self.assertGreater(info["range_ev"], 8)
        _, info0 = hdri.preview(synthetic_pano(), {"sun_ratio": 0})
        self.assertEqual(info0["sun_gain_ev"], 0)

    def test_manual_sun_position(self):
        _, info = hdri.preview(synthetic_pano(), {"sun_pos": [0.75, 0.1]})
        self.assertAlmostEqual(info["sun"]["u"], 0.75)

    def test_convert_exr_and_hdr(self):
        import OpenEXR
        import cv2
        src = os.path.join(self.dir, "pano.jpg")
        synthetic_pano(2048).save(src, quality=95)
        exr = hdri.convert(src, params={"resolution": 1024})
        self.assertTrue(exr.endswith("pano_hdri.exr"))
        with OpenEXR.File(exr) as f:
            px = f.channels()["RGB"].pixels
        self.assertEqual(px.shape, (512, 1024, 3))
        self.assertGreater(float(px.max()), 50, "el sol no tiene intensidad HDR")
        hdr = hdri.convert(src, params={"format": "hdr", "resolution": 0})
        im = cv2.imread(hdr, cv2.IMREAD_UNCHANGED)
        self.assertEqual(im.shape, (1024, 2048, 3))
        self.assertGreater(float(im.max()), 50)
        self.assertFalse([f for f in os.listdir(self.dir) if f.endswith(".part")])

    def test_never_upscales(self):
        src = os.path.join(self.dir, "small.png")
        synthetic_pano(512).save(src)
        import OpenEXR
        with OpenEXR.File(hdri.convert(src, params={"resolution": 8192})) as f:
            self.assertEqual(f.channels()["RGB"].pixels.shape[1], 512)

    def test_alpha_is_kept(self):
        import OpenEXR
        src = os.path.join(self.dir, "sincielo.png")
        img = synthetic_pano(512, alpha=True)
        a = Image.new("L", img.size, 255)
        ImageDraw.Draw(a).rectangle((0, 0, 512, 100), fill=0)   # cielo borrado
        img.putalpha(a)
        img.save(src)
        with OpenEXR.File(hdri.convert(src)) as f:
            ch = f.channels()
            self.assertIn("RGBA", ch)
            px = ch["RGBA"].pixels
            self.assertEqual(float(px[10, 10, 3]), 0.0)
            self.assertEqual(float(px[200, 10, 3]), 1.0)

    def test_rejects_non_panorama(self):
        src = os.path.join(self.dir, "foto.jpg")
        Image.new("RGB", (800, 600)).save(src)
        with self.assertRaises(hdri.HDRIError):
            hdri.convert(src)

    def test_cancel(self):
        src = os.path.join(self.dir, "c.jpg")
        synthetic_pano(1024).save(src)
        with self.assertRaises(hdri.HDRIError):
            hdri.convert(src, cancelled=lambda: True)
        self.assertEqual(sorted(os.listdir(self.dir)), ["c.jpg"])


class TestAppHDRI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = AppServer()
        cls.dir = tempfile.mkdtemp(dir=TMP)
        cls.src = os.path.join(cls.dir, "pano.jpg")
        synthetic_pano(2048).save(cls.src, quality=95)

    def test_flow(self):
        s = self.s
        with mock.patch.object(app, "pick_image", return_value=self.src):
            r = s.call("/api/hdri/pick", {})[1]
        self.assertEqual(r["size"], [2048, 1024])
        sid = r["sid"]
        pv = s.call("/api/hdri/preview", {"sid": sid, "params": {"sun_ratio": 5}, "view_ev": -8})[1]
        self.assertTrue(pv["img"].startswith("data:image/jpeg;base64,"))
        self.assertTrue(pv["info"]["sun_auto"])
        # parámetros fuera de rango o raros no rompen nada
        bad = s.call("/api/hdri/preview", {"sid": sid, "params": {"sun_ratio": "x", "sun_pos": "y",
                                                                   "resolution": 99}, "view_ev": "z"})
        self.assertEqual(bad[0], 200)
        s.call("/api/hdri/convert", {"sid": sid, "params": {"resolution": 2048, "format": "exr"}})
        st = s.wait(lambda st: st["hdri"][sid]["status"] in ("done", "error"))
        self.assertEqual(st["hdri"][sid]["status"], "done", st["hdri"][sid]["error"])
        self.assertTrue(os.path.exists(st["hdri"][sid]["out"]))
        s.call("/api/hdri/close", {"sid": sid})
        self.assertNotIn(sid, s.call("/api/state")[1]["hdri"])

    def test_open_non_panorama_gives_message(self):
        bad = os.path.join(self.dir, "foto.jpg")
        Image.new("RGB", (800, 600)).save(bad)
        with mock.patch.object(app, "pick_image", return_value=bad):
            r = self.s.call("/api/hdri/pick", {})[1]
        self.assertIn("2:1", r["error"])

    def test_pick_cancelled(self):
        with mock.patch.object(app, "pick_image", return_value=None):
            self.assertTrue(self.s.call("/api/hdri/pick", {})[1]["cancelled"])


@net
class TestAppDatesLive(unittest.TestCase):
    def test_switch_date_and_mini(self):
        s = AppServer()
        ids = s.call("/api/add", {"links": [OFFICIAL_URL]})[1]["ids"]
        st = s.wait(lambda st: any(i["id"] == ids[0] and i["status"] == "ready" and i["hasThumb"]
                                   for i in st["items"]))
        it = next(i for i in st["items"] if i["id"] == ids[0])
        self.assertGreaterEqual(len(it["dates"]), 2)
        old = it["dates"][-1]
        code, jpg = s.call(f"/api/mini/{old['id']}")
        self.assertEqual((code, jpg[:2]), (200, b"\xff\xd8"))
        s.call("/api/switch", {"id": ids[0], "panoid": old["id"]})
        st = s.wait(lambda st: any(i["id"] == ids[0] and i["status"] == "ready" and i["hasThumb"]
                                   and i["meta"]["id"] == old["id"] for i in st["items"]))
        it2 = next(i for i in st["items"] if i["id"] == ids[0])
        self.assertEqual(it2["meta"]["date"], old["date"])
        self.assertEqual(len(it2["dates"]), len(it["dates"]))
        # no se puede saltar a un ID que no es de este punto
        s.call("/api/switch", {"id": ids[0], "panoid": "AAAAAAAAAAAAAAAAAAAAAA"})
        self.assertEqual(s.call("/api/state")[1]["items"][0]["meta"]["id"], old["id"])
        s.call("/api/remove", {"id": ids[0]})


def marker_pano(w=2048, u=0.5, v=0.5, color=(255, 0, 0)):
    """Panorama gris con un punto de color en (u, v)."""
    img = Image.new("RGB", (w, w // 2), (128, 128, 128))
    d = ImageDraw.Draw(img)
    x, y = u * w, v * w / 2
    d.ellipse((x - 6, y - 6, x + 6, y + 6), fill=color)
    return np.asarray(img)


def red_center(arr):
    ys, xs = np.nonzero((arr[..., 0] > 200) & (arr[..., 1] < 80))
    return (xs.mean() / arr.shape[1], ys.mean() / arr.shape[0]) if len(xs) else None


class TestCrop(unittest.TestCase):
    def test_native_size(self):
        # 16384 px de ancho = 45.5 px/grado en el centro; 60° vertical -> ~2*2607*tan(30°)
        w, h = extras.crop_size(16384, 60, 1.5)
        self.assertAlmostEqual(h, 2 * 16384 / (2 * math.pi) * math.tan(math.radians(30)), delta=1)
        self.assertAlmostEqual(w / h, 1.5, delta=0.01)
        self.assertLessEqual(max(extras.crop_size(16384, 150, 5)), extras.MAX_SIDE)

    def test_center_of_view_is_center_of_crop(self):
        # el centro de la vista (yaw=0, pitch=0) es el centro del panorama
        out = extras.perspective_crop(marker_pano(u=0.5, v=0.5), 0, 0, 30, 1.5)
        cx, cy = red_center(out)
        self.assertAlmostEqual(cx, 0.5, delta=0.02)
        self.assertAlmostEqual(cy, 0.5, delta=0.02)

    def test_same_convention_as_viewer(self):
        # en el visor: yaw > 0 mira a la IZQUIERDA (u < 0.5) y pitch > 0 mira ARRIBA
        out = extras.perspective_crop(marker_pano(u=0.375, v=0.5), math.radians(45), 0, 30, 1)
        self.assertAlmostEqual(red_center(out)[0], 0.5, delta=0.02)
        out = extras.perspective_crop(marker_pano(u=0.5, v=0.25), 0, math.radians(45), 30, 1)
        self.assertAlmostEqual(red_center(out)[1], 0.5, delta=0.02)

    def test_wraps_around_the_seam(self):
        out = extras.perspective_crop(marker_pano(u=0.002, v=0.5), math.pi, 0, 30, 1)
        self.assertIsNotNone(red_center(out))

    def test_straight_down(self):
        out = extras.perspective_crop(marker_pano(u=0.5, v=0.999), 0, -math.pi / 2, 30, 1)
        self.assertAlmostEqual(red_center(out)[1], 0.5, delta=0.05)


class TestNadirAndFill(unittest.TestCase):
    def test_patch_only_touches_the_bottom(self):
        eq = np.asarray(synthetic_pano(1024))
        eq = eq.copy()
        eq[480:] = (255, 0, 0)                           # "trípode" rojo justo debajo
        out = extras.patch_nadir(eq)
        self.assertTrue(np.array_equal(out[:300], eq[:300]), "toca la parte de arriba")
        self.assertLess(int(out[505, 100, 0]) - int(out[505, 100, 1]), 60, "no tapa el trípode")

    def test_fill_partial(self):
        band = np.asarray(synthetic_pano(1024))[170:342]   # franja 6:1
        out = extras.fill_partial(band)
        self.assertEqual(out.shape[:2], (512, 1024))
        top = (512 - band.shape[0]) // 2
        self.assertTrue(np.array_equal(out[top:top + band.shape[0]], band), "cambia la franja original")
        self.assertGreater(out[5].mean(), 40, "deja el cielo en negro")
        self.assertTrue(extras.is_partial(1024, 172))
        self.assertFalse(extras.is_partial(1024, 512))
        self.assertIs(extras.fill_partial(np.asarray(synthetic_pano(512))).shape[0], 256)

    def test_process_with_options(self):
        out = tempfile.mkdtemp(dir=TMP)
        band = Image.fromarray(np.asarray(synthetic_pano(1024))[170:342])
        meta = fake_meta(True, 1024, 172)
        with mock.patch.object(openview, "download_third_party", return_value=(band, None)):
            p1 = openview.process("x", out, meta=meta, log=lambda *_: None)
            self.assertEqual(Image.open(p1).size, (1024, 512))
            p2 = openview.process("x", out, meta=meta, log=lambda *_: None, nadir=True)
            self.assertTrue(p2.endswith("_nadir.jpg"))
            p3 = openview.process("x", out, meta=meta, log=lambda *_: None, fill=False)
            self.assertEqual(Image.open(p3).size, (1024, 172))


class TestAppCropAndSettings(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = AppServer()
        cls.out = tempfile.mkdtemp(dir=TMP)

    def test_crop_flow(self):
        s = self.s
        s.call("/api/settings", {"outdir": self.out, "fmt": "png"})
        iid = "crop1"
        app.items[iid] = {"id": iid, "link": "x", "status": "ready", "meta": fake_meta(w=2048, h=1024)}
        app.order.insert(0, iid)
        pano = Image.fromarray(marker_pano(2048, 0.5, 0.5))
        with mock.patch.object(openview, "full_image", return_value=pano) as fi:
            cid = s.call("/api/crop", {"id": iid, "yaw": 0, "pitch": 0, "fov": 30, "aspect": 1.5})[1]["cid"]
            st = s.wait(lambda st: st["crops"][cid]["status"] != "working")
            # segundo recorte del mismo panorama: no vuelve a descargarlo
            cid2 = s.call("/api/crop", {"id": iid, "yaw": 0.2, "pitch": 0, "fov": 30, "aspect": 1})[1]["cid"]
            st = s.wait(lambda st: st["crops"][cid2]["status"] != "working")
        j = st["crops"][cid]
        self.assertEqual(j["status"], "done", j["error"])
        self.assertEqual(fi.call_count, 1)
        im = np.asarray(Image.open(j["out"]))
        self.assertEqual(list(im.shape[1::-1]), j["size"])
        self.assertAlmostEqual(red_center(im)[0], 0.5, delta=0.02)
        self.assertNotEqual(st["crops"][cid2]["out"], j["out"])
        bad = s.call("/api/crop", {"id": iid, "yaw": "nan", "pitch": 0})
        self.assertEqual(bad[0], 400)
        app.items.pop(iid)
        s.call("/api/settings", {"fmt": "jpg"})

    def test_theme_saved_and_served(self):
        self.s.call("/api/settings", {"theme": "dark"})
        self.assertIn(b'data-theme="dark"', self.s.call("/")[1])
        self.s.call("/api/settings", {"theme": "morado"})
        self.assertEqual(app.settings["theme"], "dark")
        self.s.call("/api/settings", {"theme": ""})
        self.assertNotIn(b"data-theme", self.s.call("/")[1].split(b">")[1])

    def test_window_geometry_not_settable_from_page(self):
        self.s.call("/api/settings", {"window": {"w": 1}})
        self.assertNotEqual((app.settings.get("window") or {}).get("w"), 1)

    def test_on_screen(self):
        self.assertTrue(app.on_screen({"x": 100, "y": 100, "w": 800, "h": 600}))
        self.assertFalse(app.on_screen({"x": -50000, "y": -50000, "w": 800, "h": 600}))
        self.assertFalse(app.on_screen({"x": None, "y": None, "w": 800, "h": 600}))


PHOTO_ID = "CIHM0ogKEICAgIDR_PD3Tg"   # foto normal (Can Pere Antoni, Palma)
PHOTO_URL = ("https://www.google.es/maps/place/Can+Pere+Antoni/@39.5624544,2.6622873,3a,75y,90t/"
             "data=!3m8!1e2!3m6!1s" + PHOTO_ID + "!2e10!3e12!6shttps:%2F%2Flh3.googleusercontent.com%2Fp%2F"
             + PHOTO_ID + "%3Dw203-h152-k-no!7i4032!8i3024")


def uv_of(d):
    lon = math.atan2(d[0], -d[2])
    lat = math.asin(max(-1, min(1, d[1])))
    return lon / (2 * math.pi) + 0.5, 0.5 - lat / math.pi


def dir_of(u, v):
    lon, lat = (u - 0.5) * 2 * math.pi, (0.5 - v) * math.pi
    return np.array([math.cos(lat) * math.sin(lon), math.sin(lat), -math.cos(lat) * math.cos(lon)])


class TestPhotos(unittest.TestCase):
    def test_parse_photo_metadata(self):
        m5 = [None] * 13
        m5[1] = [[None, None, 39.56, 2.66]]
        msg = [None, [10, PHOTO_ID], [10, 3, [3024, 4032]], None, None, [m5],
               [None] * 7 + [[2023, 4, 19]]]
        meta = openview._parse_pano_msg(msg)
        self.assertEqual(meta["kind"], "photo")
        self.assertEqual(meta["sizes"], [(4032, 3024)])
        self.assertIsNone(meta["tile"])
        self.assertEqual(meta["date"], "2023-04-19")

    def test_image_url_in_link(self):
        self.assertTrue(openview.link_image_url(PHOTO_URL).startswith("https://lh3.googleusercontent.com/p/"))
        self.assertIsNone(openview.link_image_url(OFFICIAL_URL))
        self.assertEqual(openview.parse_link(PHOTO_URL)[0], PHOTO_ID)

    def test_save_photo_keeps_original_jpeg(self):
        out = tempfile.mkdtemp(dir=TMP)
        img = Image.new("RGB", (400, 300), (200, 100, 50))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=91)
        meta = {**fake_meta(True, 400, 300), "kind": "photo", "sizes": [(400, 300)]}
        with mock.patch.object(openview, "download_photo", return_value=(img, buf.getvalue())):
            p = openview.process("x", out, meta=meta, log=lambda *_: None, north=True, nadir=True, level=True)
        self.assertTrue(p.endswith("_foto.jpg"))
        with open(p, "rb") as f:
            self.assertEqual(f.read(), buf.getvalue(), "recomprime la foto original")

    def test_photo_has_no_tilt_and_no_partial(self):
        self.assertEqual(openview.tilt_roll({"kind": "photo", "pitch": 95, "roll": 3}), (0.0, 0.0))


class TestLevel(unittest.TestCase):
    def test_tilt_roll_normalized(self):
        t, r = openview.tilt_roll({"kind": "pano", "pitch": 92.8, "roll": 355.0})
        self.assertAlmostEqual(t, 2.8, places=3)
        self.assertAlmostEqual(r, -5.0, places=3)
        self.assertEqual(openview.tilt_roll({"kind": "pano", "pitch": 140, "roll": 0}), (0.0, 0.0))
        self.assertEqual(openview.tilt_roll({"kind": "pano", "pitch": None, "roll": None}), (0.0, 0.0))

    def test_matrix_is_rotation(self):
        M = extras.level_matrix(-5.5, 2.9)
        self.assertTrue(np.allclose(M @ M.T, np.eye(3), atol=1e-5))
        self.assertTrue(np.allclose(extras.level_matrix(0, 0), np.eye(3)))

    def test_level_equirect_moves_points_where_expected(self):
        M = extras.level_matrix(-6, 3)
        u0, v0 = 0.62, 0.41
        eq = marker_pano(2048, u0, v0)
        out = extras.level_equirect(eq, M)
        # un punto del panorama original aparece en la dirección del mundo M^T · d
        eu, ev = uv_of(M.T @ dir_of(u0, v0))
        cu, cv = red_center(out)
        self.assertAlmostEqual(cu, eu, delta=0.004)
        self.assertAlmostEqual(cv, ev, delta=0.004)

    def test_identity_leaves_image_alone(self):
        eq = np.asarray(synthetic_pano(1024))
        out = extras.level_equirect(eq, np.eye(3, dtype=np.float32))
        self.assertLess(np.abs(out.astype(int) - eq.astype(int))[10:-10].mean(), 1.5)

    def test_crop_with_matrix_equals_crop_of_leveled(self):
        M = extras.level_matrix(4, -2)
        eq = marker_pano(2048, 0.53, 0.47)
        a = extras.perspective_crop(eq, 0, 0, 40, 1, size=(400, 400), M=M)
        b = extras.perspective_crop(extras.level_equirect(eq, M), 0, 0, 40, 1, size=(400, 400))
        ca, cb = red_center(a), red_center(b)
        self.assertAlmostEqual(ca[0], cb[0], delta=0.01)
        self.assertAlmostEqual(ca[1], cb[1], delta=0.01)

    def test_process_levels_only_when_asked(self):
        out = tempfile.mkdtemp(dir=TMP)
        img = Image.fromarray(marker_pano(1024, 0.5, 0.3))
        meta = {**fake_meta(), "pitch": 95.0, "roll": 2.0}
        with mock.patch.object(openview, "download_official", return_value=(img, None)):
            a = openview.process("x", out, meta=meta, log=lambda *_: None, level=False, fmt="png")
            ra = red_center(np.asarray(Image.open(a)))
            b = openview.process("x", out, meta=meta, log=lambda *_: None, level=True, fmt="png")
            rb = red_center(np.asarray(Image.open(b)))
        self.assertAlmostEqual(ra[1], 0.3, delta=0.01)
        self.assertGreater(abs(rb[1] - 0.3), 0.01, "no ha nivelado")


@net
class TestPhotosLive(unittest.TestCase):
    def test_photo_full_size(self):
        meta = openview.resolve(PHOTO_URL, log=lambda *_: None)
        self.assertEqual(meta["kind"], "photo")
        self.assertFalse(meta["partial"])
        out = tempfile.mkdtemp(dir=TMP)
        p = openview.process(PHOTO_URL, out, meta=meta, log=lambda *_: None)
        self.assertEqual(Image.open(p).size, (4032, 3024))

    def test_app_photo_card_and_no_crop(self):
        s = AppServer()
        ids = s.call("/api/add", {"links": [PHOTO_URL]})[1]["ids"]
        st = s.wait(lambda st: any(i["id"] == ids[0] and i["status"] == "ready" for i in st["items"]))
        it = next(i for i in st["items"] if i["id"] == ids[0])
        self.assertEqual(it["meta"]["kind"], "photo")
        self.assertIsNone(it["meta"]["level"])
        r = s.call("/api/crop", {"id": ids[0], "yaw": 0, "pitch": 0, "fov": 40, "aspect": 1})[1]
        self.assertIn("fotos", r["error"])
        s.call("/api/remove", {"id": ids[0]})

    def test_official_has_level_matrix(self):
        meta = openview.resolve(OFFICIAL_ID, log=lambda *_: None)
        rows = app.level_rows(meta)
        self.assertEqual(len(rows), 3)
        self.assertFalse(np.allclose(rows, np.eye(3)))


class TestFriendlyErrors(unittest.TestCase):
    def test_client_errors_are_not_retried(self):
        r = mock.Mock(status_code=404, content=b"")
        with mock.patch.object(openview.SESSION, "get", return_value=r) as g:
            t = time.time()
            with self.assertRaises(openview.OpenViewError):
                openview._fetch("https://x")
        self.assertEqual(g.call_count, 1)
        self.assertLess(time.time() - t, 1)

    def test_folder_errors(self):
        self.assertIn("carpeta", openview.friendly_error(PermissionError("x")).lower())
        self.assertIn("carpeta", openview.friendly_error(FileNotFoundError("x")).lower())

    def test_offline_message(self):
        import requests
        with mock.patch.object(openview.SESSION, "get", side_effect=requests.ConnectionError("boom")):
            with self.assertRaises(Exception) as cm:
                openview.resolve(OFFICIAL_URL, log=lambda *_: None)
        msg = app.friendly_error(cm.exception) if hasattr(app, "friendly_error") else str(cm.exception)
        self.assertIn("Internet", msg, "mensaje técnico sin conexión: " + msg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
