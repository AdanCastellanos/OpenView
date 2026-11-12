#!/usr/bin/env python3
"""
OpenView - aplicación de escritorio.

Lanza un pequeño servidor local y abre la interfaz en una ventana propia
(WebView2, el motor web integrado en Windows; si no, en el navegador).
Toda la descarga la hace openview.py en este mismo equipo.
"""

import json
import math
import os
import queue
import secrets
import socket
import subprocess
import sys
import threading
import time
import uuid
import base64
import io
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import extras
import hdri
import openview

APP_NAME = "OpenView"
# Dentro del .exe los archivos de la interfaz se extraen en sys._MEIPASS
HERE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
UI_FILE = os.path.join(HERE, "ui", "index.html")
_APPDATA = os.environ.get("APPDATA", os.path.expanduser("~"))
CONF_DIR = os.path.join(_APPDATA, "OpenView")
CONF_FILE = os.path.join(CONF_DIR, "settings.json")

DEFAULTS = {
    "outdir": os.path.join(os.path.expanduser("~"), "Pictures", "OpenView"),
    "fmt": "jpg",
    "north": False,
    "json": False,
    "nadir": False,       # tapar el suelo (idea 4)
    "fill": True,         # completar fotos 360 parciales hasta 2:1 (idea 3)
    "level": True,        # nivelar el horizonte con la inclinación de la cámara
    "theme": "",          # "", "light" o "dark" ("" = el de Windows)
}
WINDOW_DEFAULT = {"w": 1180, "h": 860, "x": None, "y": None, "max": False}

items = {}          # id -> dict
order = []          # ids en orden de llegada
thumbs = {}         # id -> bytes
lock = threading.Lock()
jobs = queue.Queue()
last_ping = [time.time()]
# Clave secreta por sesión: solo la página de la app puede dar órdenes al servidor
TOKEN = secrets.token_urlsafe(24)
friendly_error = openview.friendly_error


# ---------------------------------------------------------------- ajustes
def load_settings():
    try:
        with open(CONF_FILE, encoding="utf-8") as f:
            return {**DEFAULTS, **json.load(f)}
    except (OSError, ValueError):
        return dict(DEFAULTS)


def save_settings(s):
    os.makedirs(CONF_DIR, exist_ok=True)
    with open(CONF_FILE, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)


settings = load_settings()


# ---------------------------------------------------------------- trabajo
def public(item):
    m = item.get("meta") or {}
    return {
        "id": item["id"],
        "link": item["link"],
        "status": item["status"],
        "progress": item.get("progress", 0),
        "error": item.get("error"),
        "path": item.get("path"),
        "notice": item.get("notice"),
        "hasThumb": item["id"] in thumbs,
        "thumbVer": item.get("thumb_ver", 0),
        "dates": item.get("dates") or [],
        "meta": {k: m.get(k) for k in ("id", "date", "lat", "lon", "uploader",
                                       "address", "heading", "third_party", "partial", "kind")} | {
            "level": level_rows(m),
            "width": m["sizes"][-1][0] if m.get("sizes") else None,
            "height": m["sizes"][-1][1] if m.get("sizes") else None,
        } if m else None,
    }


def level_rows(meta):
    """Matriz de nivelado para el visor (3 filas)."""
    try:
        t, r = openview.tilt_roll(meta)
        return extras.level_matrix(t, r).tolist() if (t or r) else None
    except Exception:  # noqa
        return None


def set_item(iid, **kw):
    with lock:
        if iid in items:
            items[iid].update(kw)


def resolve_worker(iid):
    link = items[iid]["link"]
    try:
        meta = openview.resolve(link, log=lambda *_: None)
        # ¿Ya está en la lista? (comprobar y guardar a la vez, para que dos enlaces
        # iguales pegados juntos no entren los dos)
        with lock:
            dup = any(o["id"] != iid and o.get("meta") and o["meta"]["id"] == meta["id"]
                      for o in items.values())
            if iid not in items:
                return
            if dup:
                items[iid].update(status="error", error="Este panorama ya está en la lista.")
                return
            items[iid].update(meta=meta, status="ready", dates=merge_dates(None, meta))
        make_thumb(iid, meta)
    except Exception as e:  # noqa
        set_item(iid, status="error", error=friendly_error(e))


def make_thumb(iid, meta):
    try:
        data = openview.thumbnail(meta, 2048)
        with lock:
            if iid in items and items[iid].get("meta", {}).get("id") == meta["id"]:
                thumbs[iid] = data
                items[iid]["thumb_ver"] = items[iid].get("thumb_ver", 0) + 1
    except Exception:  # noqa - la vista previa es opcional
        pass


def merge_dates(old, meta):
    """Lista de fechas del punto (la actual incluida), de la más reciente a la más antigua."""
    seen = {d["id"]: d for d in (old or [])}
    if meta.get("date"):
        seen.setdefault(meta["id"], {"id": meta["id"], "date": meta["date"]})
    for d in meta.get("history") or []:
        seen.setdefault(d["id"], d)
    return sorted(seen.values(), key=lambda d: d["date"], reverse=True)


def switch_worker(iid, panoid):
    """Cambia una tarjeta a otra fecha del mismo punto."""
    try:
        meta = openview.resolve(panoid, log=lambda *_: None)
        with lock:
            it = items.get(iid)
            if not it:
                return
            it.update(meta=meta, status="ready", path=None, error=None, notice=None,
                      dates=merge_dates(it.get("dates"), meta))
        make_thumb(iid, meta)
    except Exception as e:  # noqa
        set_item(iid, status="ready", notice="No se pudo cambiar de fecha: " + friendly_error(e))


minis = {}


def mini(panoid):
    """Miniatura pequeña de una fecha (una sola tesela del servicio)."""
    if panoid not in minis:
        from PIL import Image
        data = openview._fetch(openview.TILE_URL.format(id=panoid, x=0, y=0, z=0), openview.TILE_HEADERS, tries=2)
        im = Image.open(io.BytesIO(data)).convert("RGB")
        box = im.point(lambda v: 255 if v > 8 else 0).convert("L").getbbox()
        if box:
            im = im.crop(box)
        buf = io.BytesIO()
        im.resize((256, 128), Image.LANCZOS).save(buf, "JPEG", quality=82)
        minis[panoid] = buf.getvalue()
    return minis[panoid]


# ---------------------------------------------------------------- recortes
views = {}        # id de tarjeta -> (id de panorama, JPEG 4096 para el visor)
crop_jobs = {}    # cid -> {...}
_full_cache = {"id": None, "img": None}
_full_lock = threading.Lock()


def view_texture(iid):
    """Textura más nítida (4096 px) para el visor 360 y para encuadrar recortes."""
    it = items.get(iid)
    if not it or not it.get("meta"):
        return None
    meta = it["meta"]
    cached = views.get(iid)
    if cached and cached[0] == meta["id"]:
        return cached[1]
    data = openview.thumbnail(meta, 4096)
    views[iid] = (meta["id"], data)
    return data


def full_array(meta, progress=None):
    """Panorama completo sin retocar; se guarda el último en memoria para varios recortes."""
    import numpy as np
    with _full_lock:
        if _full_cache["id"] == meta["id"]:
            if progress:
                progress(1.0)
            return _full_cache["img"]
        _full_cache.update(id=None, img=None)          # liberar el anterior antes de bajar otro
        img = np.asarray(openview.full_image(meta, progress))
        _full_cache.update(id=meta["id"], img=img)
        return img


def crop_worker(cid):
    from PIL import Image
    job = crop_jobs[cid]
    it = items.get(job["item"])
    try:
        if not it or not it.get("meta"):
            raise openview.OpenViewError("El panorama ya no está en la lista.")
        meta = it["meta"]
        job["stage"] = "Descargando el panorama completo…"
        eq = full_array(meta, lambda f: job.update(progress=round(0.8 * f, 3)))
        job.update(stage="Extrayendo el recorte…", progress=0.85)
        w, h = extras.crop_size(eq.shape[1], job["fov"], job["aspect"])
        M = extras.level_matrix(*openview.tilt_roll(meta)) if job["level"] else None
        arr = extras.perspective_crop(eq, job["yaw"], job["pitch"], job["fov"], job["aspect"], M=M)
        outdir = job["outdir"]
        os.makedirs(outdir, exist_ok=True)
        az = round(math.degrees(-job["yaw"]) % 360)
        el = round(math.degrees(job["pitch"]))
        base = "_".join(x for x in (meta.get("date") or "", openview.safe(meta["id"])[:16],
                                    f"recorte_{az:03d}_{el:+03d}_{round(job['fov'])}") if x)
        ext = job["fmt"]
        path = os.path.join(outdir, f"{base}.{ext}")
        n = 2
        while os.path.exists(path):
            path = os.path.join(outdir, f"{base}_{n}.{ext}")
            n += 1
        job["stage"] = "Guardando…"
        im = Image.fromarray(arr)
        tmp = path + ".part"
        try:
            if ext == "jpg":
                im.save(tmp, "JPEG", quality=97, subsampling=0)
            elif ext == "png":
                im.save(tmp, "PNG", compress_level=6)
            else:
                im.save(tmp, "TIFF", compression="tiff_lzw")
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        job.update(status="done", progress=1, out=path, size=[w, h], stage="Guardado")
    except Exception as e:  # noqa
        job.update(status="error", error=friendly_error(e))


def public_crop(j):
    return {k: j.get(k) for k in ("cid", "item", "status", "progress", "stage", "out", "error", "size")}


# ---------------------------------------------------------------- HDRI
hdri_jobs = {}   # sid -> {src, name, small, status, progress, out, error, cancel}


def hdri_open(path):
    from PIL import Image
    if not path or not os.path.isfile(path):
        raise hdri.HDRIError("No encuentro el archivo.")
    im = Image.open(path)
    w, h = im.size
    if abs(w / h - 2) > 0.05:
        raise hdri.HDRIError(f"La imagen no es un panorama 360 (2:1): mide {w} x {h}.")
    if im.format == "JPEG":
        im.draft("RGB", (2048, 1024))  # decodifica rápido a tamaño reducido
    small = im.convert("RGB").resize((1024, 512), Image.LANCZOS)
    sid = uuid.uuid4().hex[:10]
    hdri_jobs[sid] = {"sid": sid, "src": path, "name": os.path.basename(path), "small": small,
                      "size": [w, h], "status": "idle", "progress": 0, "out": None, "error": None}
    return {"sid": sid, "name": os.path.basename(path), "size": [w, h]}


def clean_hdri_params(d):
    def num(k, lo, hi, default):
        try:
            return min(hi, max(lo, float(d.get(k, default))))
        except (TypeError, ValueError):
            return default
    pos = d.get("sun_pos")
    if not (isinstance(pos, list) and len(pos) == 2 and all(isinstance(v, (int, float)) for v in pos)):
        pos = None
    else:
        pos = [min(1.0, max(0.0, float(pos[0]))), min(1.0, max(0.0, float(pos[1])))]
    res = d.get("resolution", 8192)
    return {
        "sun_ratio": num("sun_ratio", 0, 20, hdri.DEFAULTS["sun_ratio"]),
        "sun_size": num("sun_size", 0.3, 5, hdri.DEFAULTS["sun_size"]),
        "highlights": num("highlights", 1, 10, hdri.DEFAULTS["highlights"]),
        "exposure": num("exposure", -5, 5, 0.0),
        "resolution": res if res in (2048, 4096, 8192, 0) else 8192,
        "format": d.get("format") if d.get("format") in ("exr", "hdr") else "exr",
        "sun_pos": pos,
    }


def hdri_worker(sid, params):
    job = hdri_jobs[sid]

    def prog(f):
        job["progress"] = round(f, 3)

    try:
        out = hdri.convert(job["src"], params=params, progress=prog,
                           cancelled=lambda: job.get("cancel"))
        job.update(status="done", out=out, progress=1)
    except Exception as e:  # noqa
        msg = str(e) if isinstance(e, hdri.HDRIError) else friendly_error(e)
        job.update(status="cancelled" if job.get("cancel") else "error", error=msg)


def public_hdri(j):
    return {k: j.get(k) for k in ("sid", "name", "size", "status", "progress", "out", "error")}


def download_worker():
    while True:
        iid, opts = jobs.get()
        item = items.get(iid)
        if not item or item["status"] != "queued":
            continue
        set_item(iid, status="downloading", progress=0, cancel=False)

        def progress(p, iid=iid):
            it = items.get(iid)
            if it is None or it.get("cancel"):
                raise openview.Cancelled("Descarga cancelada.")
            it["progress"] = round(p, 3)

        try:
            path = openview.process(
                item["link"], opts["outdir"], opts["fmt"], opts["north"],
                log=lambda *_: None, progress=progress,
                meta=item["meta"], save_json=opts["json"],
                nadir=opts["nadir"], fill=opts["fill"], level=opts["level"])
            set_item(iid, status="done", progress=1, path=path)
        except openview.Cancelled:
            set_item(iid, status="ready", progress=0, cancel=False, notice="Descarga cancelada.")
        except Exception as e:  # noqa
            set_item(iid, status="error", error=friendly_error(e))


threading.Thread(target=download_worker, daemon=True).start()


WINDOW = [None]  # ventana nativa (pywebview), si se usa


def pick_image():
    types = ("Panoramas (*.jpg;*.jpeg;*.png;*.tif;*.tiff)",)
    if WINDOW[0] is not None:
        import webview
        res = WINDOW[0].create_file_dialog(webview.FileDialog.OPEN, directory=settings["outdir"]
                                           if os.path.isdir(settings["outdir"]) else "",
                                           file_types=types)
        return os.path.normpath(res[0]) if res else None
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    path = filedialog.askopenfilename(title="Panorama para convertir a HDRI",
                                      filetypes=[("Panoramas", "*.jpg *.jpeg *.png *.tif *.tiff")])
    root.destroy()
    return os.path.normpath(path) if path else None


def pick_folder(initial):
    if WINDOW[0] is not None:
        import webview
        res = WINDOW[0].create_file_dialog(
            webview.FileDialog.FOLDER,
            directory=initial if os.path.isdir(initial) else "")
        return os.path.normpath(res[0]) if res else None
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    path = filedialog.askdirectory(initialdir=initial if os.path.isdir(initial) else None,
                                   title="Carpeta donde guardar los panoramas")
    root.destroy()
    return os.path.normpath(path) if path else None


def reveal(path, select=True):
    if sys.platform != "win32":
        webbrowser.open("file://" + os.path.dirname(path))
        return
    if select:
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    else:
        os.startfile(path)


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code=200, body=b"", ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            d = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return None
        return d if isinstance(d, dict) else None

    def _allowed(self):
        """Rechaza órdenes que no vengan de la propia ventana de la app."""
        origin = self.headers.get("Origin")
        if origin and origin != "http://" + self.headers.get("Host", ""):
            return False
        return secrets.compare_digest(self.headers.get("X-Token", ""), TOKEN)

    def do_GET(self):
        p = self.path.split("?")[0]
        if p in ("/", "/index.html"):
            with open(UI_FILE, "rb") as f:
                html = f.read().replace(b"__APP_TOKEN__", TOKEN.encode())
            if settings.get("theme") in ("light", "dark"):
                html = html.replace(b'<html lang="es">',
                                    f'<html lang="es" data-theme="{settings["theme"]}">'.encode(), 1)
            return self._send(200, html, "text/html; charset=utf-8")
        if p == "/api/state":
            if not self._allowed():
                return self._send(403, {"error": "no permitido"})
            with lock:
                return self._send(200, {"items": [public(items[i]) for i in order if i in items],
                                        "settings": settings,
                                        "hdri": {k: public_hdri(v) for k, v in hdri_jobs.items()},
                                        "crops": {k: public_crop(v) for k, v in crop_jobs.items()}})
        if p.startswith("/api/view/"):
            try:
                data = view_texture(p.rsplit("/", 1)[-1])
            except Exception:  # noqa
                data = None
            return self._send(200, data, "image/jpeg") if data else self._send(404, {})
        if p.startswith("/api/mini/"):
            try:
                return self._send(200, mini(p.rsplit("/", 1)[-1]), "image/jpeg")
            except Exception:  # noqa
                return self._send(404, {})
        if p.startswith("/api/thumb/"):
            data = thumbs.get(p.rsplit("/", 1)[-1])
            return self._send(200, data, "image/jpeg") if data else self._send(404, {})
        self._send(404, {"error": "no encontrado"})

    def do_POST(self):
        p = self.path
        if not self._allowed():
            return self._send(403, {"error": "no permitido"})
        d = self._json()
        if d is None:
            return self._send(400, {"error": "petición no válida"})
        if p == "/api/ping":
            last_ping[0] = time.time()
            return self._send(200, {})
        if p == "/api/add":
            new = []
            for link in d.get("links", [])[:200]:
                if not isinstance(link, str):
                    continue
                link = link.strip()
                if not link:
                    continue
                iid = uuid.uuid4().hex[:10]
                with lock:
                    items[iid] = {"id": iid, "link": link, "status": "resolving"}
                    order.insert(0, iid)
                threading.Thread(target=resolve_worker, args=(iid,), daemon=True).start()
                new.append(iid)
            return self._send(200, {"ids": new})
        if p == "/api/remove":
            with lock:
                it = items.pop(d.get("id"), None)
                thumbs.pop(d.get("id"), None)
                if it:
                    it["cancel"] = True  # si se estaba descargando, se detiene
            return self._send(200, {})
        if p == "/api/switch":
            it = items.get(d.get("id"))
            panoid = d.get("panoid")
            if it and isinstance(panoid, str) and it["status"] in ("ready", "done", "error") \
                    and any(x["id"] == panoid for x in it.get("dates") or []):
                with lock:
                    thumbs.pop(it["id"], None)
                    it.update(status="resolving")
                threading.Thread(target=switch_worker, args=(it["id"], panoid), daemon=True).start()
            return self._send(200, {})
        if p in ("/api/hdri/open", "/api/hdri/pick"):
            path = None
            if p == "/api/hdri/pick":
                path = pick_image()
                if not path:
                    return self._send(200, {"cancelled": True})
            else:
                it = items.get(d.get("id"))
                path = it and it.get("path")
            try:
                return self._send(200, hdri_open(path))
            except Exception as e:  # noqa
                msg = str(e) if isinstance(e, hdri.HDRIError) else friendly_error(e)
                return self._send(200, {"error": msg})
        if p == "/api/crop":
            it = items.get(d.get("id"))
            try:
                yaw, pitch = float(d.get("yaw", 0)), float(d.get("pitch", 0))
                fov, aspect = float(d.get("fov", 60)), float(d.get("aspect", 1.5))
            except (TypeError, ValueError):
                return self._send(400, {"error": "petición no válida"})
            if not it or not it.get("meta"):
                return self._send(200, {"error": "Este panorama aún no está listo."})
            if it["meta"].get("kind") == "photo":
                return self._send(200, {"error": "Los recortes son para panoramas 360, no para fotos."})
            if not (math.isfinite(yaw) and math.isfinite(pitch)):
                return self._send(400, {"error": "petición no válida"})
            cid = uuid.uuid4().hex[:10]
            crop_jobs[cid] = {"cid": cid, "item": it["id"], "status": "working", "progress": 0,
                              "stage": "Preparando…", "yaw": yaw,
                              "pitch": max(-math.pi / 2, min(math.pi / 2, pitch)),
                              "fov": max(5.0, min(120.0, fov)), "aspect": max(0.2, min(5.0, aspect)),
                              "outdir": settings["outdir"], "fmt": settings["fmt"],
                              "level": settings["level"]}
            threading.Thread(target=crop_worker, args=(cid,), daemon=True).start()
            return self._send(200, {"cid": cid})
        if p == "/api/hdri/preview":
            job = hdri_jobs.get(d.get("sid"))
            if not job:
                return self._send(404, {})
            try:
                ev = min(4.0, max(-16.0, float(d.get("view_ev", 0))))
            except (TypeError, ValueError):
                ev = 0.0
            jpg, info = hdri.preview(job["small"], clean_hdri_params(d.get("params") or {}), ev)
            return self._send(200, {"img": "data:image/jpeg;base64," + base64.b64encode(jpg).decode(),
                                    "info": info})
        if p == "/api/hdri/convert":
            job = hdri_jobs.get(d.get("sid"))
            if job and job["status"] != "working":
                job.update(status="working", progress=0, out=None, error=None, cancel=False)
                threading.Thread(target=hdri_worker,
                                 args=(job["sid"], clean_hdri_params(d.get("params") or {})),
                                 daemon=True).start()
            return self._send(200, {})
        if p == "/api/hdri/cancel":
            job = hdri_jobs.get(d.get("sid"))
            if job and job["status"] == "working":
                job["cancel"] = True
            return self._send(200, {})
        if p == "/api/hdri/close":
            job = hdri_jobs.get(d.get("sid"))
            if job and job["status"] != "working":
                hdri_jobs.pop(job["sid"], None)
            return self._send(200, {})
        if p == "/api/cancel":
            with lock:
                it = items.get(d.get("id"))
                if it and it["status"] == "queued":
                    it.update(status="ready", notice="Descarga cancelada.")
                elif it and it["status"] == "downloading":
                    it["cancel"] = True
            return self._send(200, {})
        if p == "/api/clear-done":
            with lock:
                for i in [i for i, it in items.items() if it["status"] in ("done", "error")]:
                    items.pop(i)
                    thumbs.pop(i, None)
            return self._send(200, {})
        if p == "/api/settings":
            clean = {k: d[k] for k in DEFAULTS
                     if k in d and isinstance(d[k], type(DEFAULTS[k]))}
            if clean.get("fmt", "jpg") not in ("jpg", "png", "tif"):
                clean.pop("fmt")
            if clean.get("theme", "") not in ("", "light", "dark"):
                clean.pop("theme")
            settings.update(clean)
            save_settings(settings)
            return self._send(200, settings)
        if p == "/api/pick-folder":
            path = pick_folder(settings["outdir"])
            if path:
                settings["outdir"] = path
                save_settings(settings)
            return self._send(200, {"path": path})
        if p == "/api/download":
            opts = {k: settings[k] for k in DEFAULTS}
            for iid in d.get("ids", []):
                it = items.get(iid)
                if it and it["status"] in ("ready", "done", "error") and it.get("meta"):
                    set_item(iid, status="queued", progress=0, error=None, notice=None)
                    jobs.put((iid, dict(opts)))
            return self._send(200, {})
        if p == "/api/open":
            it = items.get(d.get("id"))
            target = it and it.get("path")
            if d.get("what") == "crop":
                job = crop_jobs.get(d.get("cid"))
                if job and job.get("out") and os.path.exists(job["out"]):
                    reveal(job["out"], select=d.get("mode") != "file")
                return self._send(200, {})
            if d.get("what") == "hdri":
                job = hdri_jobs.get(d.get("sid"))
                if job and job.get("out") and os.path.exists(job["out"]):
                    reveal(job["out"])
                return self._send(200, {})
            if d.get("what") == "outdir":
                os.makedirs(settings["outdir"], exist_ok=True)
                reveal(settings["outdir"], select=False)
            elif target and os.path.exists(target):
                reveal(target, select=d.get("what") != "file")
            return self._send(200, {})
        self._send(404, {"error": "no encontrado"})


# ---------------------------------------------------------------- ventana
def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def busy():
    return (any(it["status"] in ("queued", "downloading") for it in list(items.values()))
            or any(j["status"] == "working" for j in list(hdri_jobs.values()))
            or any(j["status"] == "working" for j in list(crop_jobs.values())))


def on_screen(g):
    """¿La ventana guardada se vería en alguna pantalla conectada?"""
    if g.get("x") is None or g.get("y") is None:
        return False
    if sys.platform != "win32":
        return True
    import ctypes
    from ctypes import wintypes
    pt = wintypes.POINT(int(g["x"] + min(g["w"], 400) // 2), int(g["y"] + 20))
    return bool(ctypes.windll.user32.MonitorFromPoint(pt, 0))  # 0 = ninguna -> NULL


def run_native_window(url):
    """Ventana propia con WebView2 (el motor web de Windows): sin cuentas,
    sin sincronización y sin historial. Devuelve False si no está disponible."""
    try:
        import webview
    except ImportError:
        return False
    def on_closing():
        if not busy():
            return True
        n = (sum(it["status"] in ("queued", "downloading") for it in list(items.values()))
             + sum(j["status"] == "working" for j in list(hdri_jobs.values())))
        plural = n > 1
        ok = WINDOW[0].create_confirmation_dialog(
            "Descargas en curso",
            f"Hay {n} descarga{'s' if plural else ''} sin terminar. Si cierras ahora "
            f"se cancelará{'n' if plural else ''}. ¿Cerrar de todas formas?")
        if ok:
            stop_all()
            save_window()
        return bool(ok)

    # Tamaño y posición de la última vez (si sigue cabiendo en alguna pantalla)
    g = {**WINDOW_DEFAULT, **(settings.get("window") or {})}
    if not (isinstance(g["w"], int) and isinstance(g["h"], int)):
        g.update(w=WINDOW_DEFAULT["w"], h=WINDOW_DEFAULT["h"])
    g["w"], g["h"] = max(640, g["w"]), max(520, g["h"])
    if not on_screen(g):
        g["x"] = g["y"] = None
    geom = dict(g)

    def on_resized(w, h):
        if not geom["max"]:
            geom.update(w=int(w), h=int(h))

    def on_moved(x, y):
        if not geom["max"] and x > -3000 and y > -3000:  # Windows usa -32000 al minimizar
            geom.update(x=int(x), y=int(y))

    def save_window():
        settings["window"] = dict(geom)
        try:
            save_settings(settings)
        except OSError:
            pass

    def closing():
        ok = on_closing()
        if ok:
            save_window()
        return ok

    try:
        WINDOW[0] = webview.create_window(APP_NAME, url, width=g["w"], height=g["h"],
                                          x=g["x"], y=g["y"], maximized=bool(g["max"]),
                                          min_size=(640, 520))
        WINDOW[0].events.closing += closing
        WINDOW[0].events.resized += on_resized
        WINDOW[0].events.moved += on_moved
        WINDOW[0].events.maximized += lambda: geom.update(max=True)
        WINDOW[0].events.restored += lambda: geom.update(max=False)
        webview.start(gui="edgechromium", private_mode=False,
                      storage_path=os.path.join(CONF_DIR, "webview"))
    except Exception:  # noqa
        WINDOW[0] = None
        return False
    WINDOW[0] = None
    return True


def stop_all():
    """Cancela todo y espera un momento a que se detengan las descargas."""
    with lock:
        for it in items.values():
            if it["status"] in ("queued", "downloading"):
                it["cancel"] = True
                if it["status"] == "queued":
                    it["status"] = "ready"
        for j in hdri_jobs.values():
            if j["status"] == "working":
                j["cancel"] = True
    t = time.time()
    while busy() and time.time() - t < 10:
        time.sleep(0.2)


def selftest(report):
    """Comprobación rápida de que el .exe trae todo lo necesario (EXR, HDR, ventana)."""
    import tempfile
    from PIL import Image, ImageDraw
    lines = []
    try:
        d = tempfile.mkdtemp(prefix="openview-selftest-")
        src = os.path.join(d, "p.jpg")
        img = Image.new("RGB", (1024, 512), (90, 140, 210))
        ImageDraw.Draw(img).ellipse((300, 90, 310, 100), fill="white")
        img.save(src)
        for fmt in ("exr", "hdr"):
            out = hdri.convert(src, params={"format": fmt, "resolution": 1024})
            lines.append(f"{fmt} OK {os.path.getsize(out)} bytes")
        import webview  # noqa: F401
        lines.append("webview OK")
        lines.append("RESULT OK")
    except Exception as e:  # noqa
        lines.append(f"RESULT FAIL {type(e).__name__}: {e}")
    with open(report, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main():
    if "--selftest" in sys.argv:
        selftest(sys.argv[sys.argv.index("--selftest") + 1])
        return
    port = free_port()
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"{APP_NAME} en {url}", flush=True)

    if "--no-window" in sys.argv:
        srv.serve_forever()
        return

    threading.Thread(target=srv.serve_forever, daemon=True).start()
    if run_native_window(url):
        os._exit(0)

    # Plan B: navegador por defecto. Se cierra al dejar de recibir latidos.
    webbrowser.open(url)
    last_ping[0] = time.time() + 30
    while True:
        time.sleep(5)
        if not busy() and time.time() - last_ping[0] > 20:
            os._exit(0)


if __name__ == "__main__":
    main()
