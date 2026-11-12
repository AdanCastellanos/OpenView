#!/usr/bin/env python3
"""
OpenView (openview.py) - Descarga panoramas 360 de mapas (vista de calle oficial y
fotos 360 de usuarios / "puntos aislados") a la máxima resolución
disponible, como imagen equirectangular 2:1 lista para editar y usar en 3ds Max.

Uso:
    python openview.py "<enlace de Maps>" -o "<carpeta>"
    python openview.py enlace1 enlace2 ... -o salida --formato png
    python openview.py --lista enlaces.txt -o salida
    python openview.py "<enlace>" -o salida --json   (guarda también los datos)

Dependencias: requests, Pillow   (pip install requests pillow)
"""

import argparse
import io
import json
import math
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import unquote, urlparse, parse_qs, urljoin

import requests
from PIL import Image

Image.MAX_IMAGE_PIXELS = None  # panoramas de 16384x8192 o más

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:140.0) Gecko/20100101 Firefox/140.0"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA})


class OpenViewError(Exception):
    pass


# --------------------------------------------------------------------------
# Protobuf-en-URL que usa el servicio de mapas (formato !1m2!1s...)
# --------------------------------------------------------------------------
class Enum_:
    def __init__(self, v):
        self.v = v


def _pb_field(tag, value):
    if isinstance(value, list):
        n, s = 0, ""
        for item in value:
            c, t = _pb_field(tag, item)
            n += c
            s += t
        return n, s
    if isinstance(value, dict):
        n, s = _pb_msg(value)
        return n + 1, f"!{tag}m{n}" + s
    if isinstance(value, bool):
        return 1, f"!{tag}b{1 if value else 0}"
    if isinstance(value, Enum_):
        return 1, f"!{tag}e{value.v}"
    if isinstance(value, int):
        return 1, f"!{tag}i{value}"
    if isinstance(value, float):
        return 1, f"!{tag}d{value}"
    return 1, f"!{tag}s{value}"


def _pb_msg(fields):
    n, s = 0, ""
    for tag, value in fields.items():
        c, t = _pb_field(tag, value)
        n += c
        s += t
    return n, s


# --------------------------------------------------------------------------
# Identificación del panorama a partir del enlace
# --------------------------------------------------------------------------
def is_third_party(panoid):
    """Fotos 360 de usuarios (AF1Qip..., CIHM0og...) frente a la vista de calle oficial."""
    return panoid.startswith("CIHM0og") or len(panoid) > 22


def resolve_short_link(url):
    """Sigue maps.app.goo.gl / goo.gl / consent.google.com hasta la URL real de Maps."""
    for _ in range(10):
        host = urlparse(url).netloc
        if "consent.google" in host:
            cont = parse_qs(urlparse(url).query).get("continue")
            if cont:
                url = cont[0]
                continue
        if re.search(r"(^|\.)google\.[a-z.]+$", host) and "/maps" in url:
            return url
        r = SESSION.get(url, allow_redirects=False, timeout=20)
        loc = r.headers.get("Location")
        if not loc:
            return url
        url = urljoin(url, loc)
    return url


def parse_link(text):
    """Devuelve (panoid o None, lat o None, lon o None)."""
    text = text.strip().strip('"').strip("'")
    if not text:
        raise OpenViewError("Enlace vacío.")
    # ¿Es directamente un ID de panorama?
    if re.fullmatch(r"[A-Za-z0-9_\-]{20,}", text) and not text.startswith("http"):
        return text, None, None
    if not text.startswith("http"):
        text = "https://" + text
    url = resolve_short_link(text)
    dec = unquote(url)

    panoid = None
    m = re.search(r"!1s([^!]+)!2e(\d+)", dec)
    if m:
        panoid = m.group(1)
    if not panoid:
        q = parse_qs(urlparse(url).query)
        for k in ("panoid", "pano"):
            if k in q:
                panoid = q[k][0]
                break
    lat = lon = None
    m = re.search(r"@(-?\d+\.\d+),(-?\d+\.\d+)", dec)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
    if not m:
        q = parse_qs(urlparse(url).query)
        if "viewpoint" in q:
            lat, lon = map(float, q["viewpoint"][0].split(","))
    if not panoid and lat is None:
        raise OpenViewError(f"No encuentro un panorama en el enlace:\n  {url}")
    return panoid, lat, lon


# --------------------------------------------------------------------------
# Metadatos
# --------------------------------------------------------------------------
MAPS_HEADERS = {
    "Accept": "*/*",
    "Referer": "https://www.google.com/",
    "User-Agent": UA,
}


def _photometa_url(panoid, lang="es"):
    t = [Enum_(x) for x in (1, 2, 3, 4, 5, 6, 8, 12)]
    msg = {
        1: {1: "maps_sv.tactile", 11: {2: {1: True}}},
        2: {1: lang, 2: lang},
        3: {1: {1: Enum_(10 if is_third_party(panoid) else 2), 2: panoid}},
        4: {
            1: t,
            2: {1: Enum_(1)},
            4: {1: 48},
            5: [{}],
            6: [{}],
            9: {1: [
                {1: Enum_(2), 2: True, 3: Enum_(2)},
                {1: Enum_(2), 2: False, 3: Enum_(3)},
                {1: Enum_(3), 2: True, 3: Enum_(2)},
                {1: Enum_(3), 2: False, 3: Enum_(3)},
                {1: Enum_(8), 2: False, 3: Enum_(3)},
                {1: Enum_(1), 2: False, 3: Enum_(3)},
                {1: Enum_(4), 2: False, 3: Enum_(3)},
                {1: Enum_(10), 2: True, 3: Enum_(2)},
                {1: Enum_(10), 2: False, 3: Enum_(3)},
            ]},
            11: {3: {4: True}},
        },
    }
    return (f"https://www.google.com/maps/photometa/v1?authuser=0&hl={lang}&gl={lang}&pb="
            + _pb_msg(msg)[1])


def _get(lst, *idx):
    try:
        for i in idx:
            lst = lst[i]
        return lst
    except (IndexError, TypeError, KeyError):
        return None


def _parse_pano_msg(msg):
    # msg[2][1]: 2 = panorama 360 equirectangular, 3 = foto normal (plana)
    kind = "photo" if _get(msg, 2, 1) == 3 or not _get(msg, 2, 3) else "pano"
    if kind == "photo":
        hw = _get(msg, 2, 2)
        if not hw:
            raise OpenViewError("El servicio de mapas no da el tamaño de esta foto.")
        sizes, tile = [(hw[1], hw[0])], None
    else:
        sizes = [(s[0][1], s[0][0]) for s in msg[2][3][0]]  # (ancho, alto) por zoom
        tile = (msg[2][3][1][0], msg[2][3][1][1])
    date = _get(msg, 6, 7)
    # Otras fechas del mismo punto: msg[5][0][8] = [[índice, [año, mes]], ...]
    # y el índice apunta a la lista de panoramas cercanos msg[5][0][3][0]
    history = []
    others = _get(msg, 5, 0, 3, 0) or []
    for entry in _get(msg, 5, 0, 8) or []:
        idx, ym = _get(entry, 0), _get(entry, 1)
        pid = _get(others, idx, 0, 1) if isinstance(idx, int) else None
        if pid and ym:
            history.append({"id": pid, "date": "-".join(f"{d:02d}" for d in ym)})
    return {
        "history": history,
        "kind": kind,
        "id": msg[1][1],
        "sizes": sizes,
        "tile": tile,
        "lat": _get(msg, 5, 0, 1, 0, 2),
        "lon": _get(msg, 5, 0, 1, 0, 3),
        "heading": _get(msg, 5, 0, 1, 2, 0),
        "pitch": _get(msg, 5, 0, 1, 2, 1),
        "roll": _get(msg, 5, 0, 1, 2, 2),
        "date": "-".join(f"{d:02d}" for d in date) if date else None,
        "uploader": _get(msg, 4, 1, 0, 0, 0),
        "address": " / ".join(a[0] for a in (_get(msg, 3, 2) or []) if a),
    }


def get_metadata_by_id(panoid):
    r = SESSION.get(_photometa_url(panoid), headers=MAPS_HEADERS, timeout=30)
    if r.status_code == 400:
        raise OpenViewError(f"El servicio de mapas no reconoce el panorama {panoid}. Revisa que el enlace "
                         "esté completo (copia la URL de la barra del navegador estando "
                         "dentro de la vista de calle).")
    r.raise_for_status()
    data = json.loads(r.text[4:])
    code = _get(data, 1, 0, 0, 0)
    if code not in (1, 3):
        raise OpenViewError(f"El servicio de mapas no devuelve el panorama {panoid} (puede haber sido "
                         "retirado o el ID ha cambiado).")
    return _parse_pano_msg(data[1][0])


def find_nearest(lat, lon, radius=50, third_party=False):
    body = [
        ["apiv3", None, None, None, "US"],
        [[None, None, lat, lon], float(radius)],
        [None, ["es", "ES"], None, None, None, None, None, None, [2], None,
         [[[10 if third_party else 2, True, 2]]]],
        [[1, 2, 3, 4, 6, 8, 12], None, None, None, [], []],
    ]
    r = SESSION.post(
        "https://maps.googleapis.com/$rpc/google.internal.maps.mapsjs.v1."
        "MapsJsInternalService/SingleImageSearch",
        data=json.dumps(body),
        headers={"content-type": "application/json+protobuf"}, timeout=30)
    r.raise_for_status()
    data = r.json()
    if _get(data, 0, 0) != 0:
        return None
    return _parse_pano_msg(data[1])


# --------------------------------------------------------------------------
# Descarga
# --------------------------------------------------------------------------
TILE_URL = ("https://streetviewpixels-pa.googleapis.com/v1/tile?cb_client=maps_sv.tactile"
            "&panoid={id}&x={x}&y={y}&zoom={z}")
TILE_HEADERS = {"Origin": "https://www.google.com", "Referer": "https://www.google.com/",
                "User-Agent": UA}


class Cancelled(Exception):
    """La descarga se canceló a petición del usuario."""


def friendly_error(e):
    """Convierte cualquier excepción en un mensaje comprensible en español."""
    if isinstance(e, (OpenViewError, Cancelled)):
        return str(e) or "Cancelado."
    if isinstance(e, requests.ConnectionError):
        return "No hay conexión a Internet o el servicio de mapas no responde. Revisa la conexión e inténtalo de nuevo."
    if isinstance(e, requests.Timeout):
        return "El servicio de mapas ha tardado demasiado en responder. Inténtalo de nuevo en un momento."
    if isinstance(e, requests.HTTPError):
        code = e.response.status_code if e.response is not None else "?"
        return f"El servicio de mapas ha rechazado la petición (error {code}). Inténtalo más tarde."
    if isinstance(e, PermissionError):
        return ("No se puede escribir en la carpeta de destino (permiso denegado). "
                "Elige otra carpeta o cierra el archivo si lo tienes abierto en otro programa.")
    if isinstance(e, (FileNotFoundError, NotADirectoryError)) or (
            isinstance(e, OSError) and getattr(e, "winerror", None) in (3, 123, 267)):
        return "La carpeta de destino no existe o no es válida. Elige otra carpeta."
    if isinstance(e, OSError) and getattr(e, "errno", None) == 28:
        return "No queda espacio en el disco de destino."
    if isinstance(e, MemoryError):
        return "No hay memoria suficiente para montar el panorama. Cierra otros programas o usa menos resolución."
    return f"Error inesperado: {e}"


def _fetch(url, headers=None, tries=4):
    last = None
    for i in range(tries):
        try:
            r = SESSION.get(url, headers=headers, timeout=60)
            if r.status_code == 200 and r.content:
                return r.content
            if 400 <= r.status_code < 500 and r.status_code != 429:
                raise OpenViewError(f"El servicio de mapas no ha entregado la imagen (error {r.status_code}).")
            last = None
        except requests.RequestException as e:
            last = e
        time.sleep(1 + i)
    if last is not None:
        raise last
    raise OpenViewError("El servicio de mapas no ha entregado la imagen tras varios intentos. Inténtalo más tarde.")


def download_official(meta, zoom, log, progress=None):
    w, h = meta["sizes"][zoom]
    tw, th = meta["tile"]
    cols, rows = math.ceil(w / tw), math.ceil(h / th)
    total = cols * rows
    log(f"  Descargando {total} teselas ({cols}x{rows}) ...")
    if progress:
        progress(0.0)
    pano = Image.new("RGB", (w, h))
    done = [0]
    lock = threading.Lock()

    def job(xy):
        x, y = xy
        data = _fetch(TILE_URL.format(id=meta["id"], x=x, y=y, z=zoom), TILE_HEADERS)
        img = Image.open(io.BytesIO(data)).convert("RGB")
        if img.size != (tw, th):  # por seguridad, nunca dejar huecos
            img = img.resize((tw, th), Image.LANCZOS)
        with lock:
            pano.paste(img, (x * tw, y * th))
            done[0] += 1
            if progress:
                progress(done[0] / total)
            if done[0] % max(1, total // 10) == 0 or done[0] == total:
                log(f"  {done[0]}/{total} teselas")

    ex = ThreadPoolExecutor(max_workers=16)
    futures = [ex.submit(job, (x, y)) for y in range(rows) for x in range(cols)]
    try:
        for f in as_completed(futures):
            f.result()  # propaga el primer fallo (o la cancelación)
    except BaseException:
        ex.shutdown(wait=True, cancel_futures=True)  # no seguir bajando teselas
        raise
    ex.shutdown(wait=True)
    return pano, None


def download_third_party(meta, log, zoom=-1):
    w, h = meta["sizes"][zoom]
    log(f"  Descargando imagen completa de la foto 360 ...")
    urls = [f"https://lh3.ggpht.com/jsapi2/a/b/c/w{w}-h{h}/{meta['id']}",
            f"https://lh3.googleusercontent.com/p/{meta['id']}=w{w}-h{h}-k-no"]
    err = None
    for u in urls:
        try:
            data = _fetch(u, tries=2)
            img = Image.open(io.BytesIO(data))
            img.load()
            return img.convert("RGB"), data
        except requests.RequestException:
            raise
        except Exception as e:  # noqa
            err = e
    raise OpenViewError(f"No se pudo descargar la foto 360 ({friendly_error(err)})")


def roll_to_north(img, heading_deg):
    """Desplaza la imagen para que el centro mire al norte geográfico."""
    w = img.width
    shift = int(round((heading_deg % 360) / 360.0 * w)) % w
    if shift == 0:
        return img
    out = Image.new(img.mode, img.size)
    out.paste(img.crop((0, 0, w - shift, img.height)), (shift, 0))
    out.paste(img.crop((w - shift, 0, w, img.height)), (0, 0))
    return out


def safe(s):
    return re.sub(r"[^A-Za-z0-9_\-]+", "_", s)[:40].strip("_")


def resolve(link, log=print):
    """Enlace -> metadatos del panorama."""
    panoid, lat, lon = parse_link(link)
    if panoid:
        log(f"  Panorama: {panoid}")
        meta = get_metadata_by_id(panoid)
    else:
        log(f"  Sin ID en el enlace; buscando el panorama más cercano a {lat},{lon}")
        meta = find_nearest(lat, lon) or find_nearest(lat, lon, third_party=True)
        if not meta:
            raise OpenViewError("No hay vista de calle en esa ubicación.")
    meta["third_party"] = is_third_party(meta["id"])
    meta.setdefault("kind", "pano")
    w, h = meta["sizes"][-1]
    # Algunas fotos 360 no cubren la esfera completa (no son 2:1)
    meta["partial"] = meta["kind"] == "pano" and abs(w / h - 2.0) > 0.02
    meta["image_url"] = link_image_url(link)
    return meta


def link_image_url(link):
    """URL de la imagen que algunos enlaces de fotos llevan dentro (!6s...)."""
    m = re.search(r"!6s(https?:[^!]+)", unquote(link or ""))
    return m.group(1) if m else None


def tilt_roll(meta):
    """Inclinación y alabeo de la cámara (grados) según el servicio. 0, 0 = ya nivelada."""
    t, r = meta.get("pitch"), meta.get("roll")
    if meta.get("kind") == "photo" or t is None or r is None:
        return 0.0, 0.0
    t = float(t) - 90.0
    r = (float(r) + 180.0) % 360.0 - 180.0
    if abs(t) > 25 or abs(r) > 25:   # valores raros: mejor no tocar nada
        return 0.0, 0.0
    return t, r


def download_photo(meta, log):
    """Foto normal de la aplicación de mapas a su tamaño original."""
    w, h = meta["sizes"][-1]
    log("  Descargando la foto a tamaño original ...")
    urls = [f"https://lh3.ggpht.com/jsapi2/a/b/c/w{w}-h{h}/{meta['id']}",
            f"https://lh3.googleusercontent.com/p/{meta['id']}=w{w}-h{h}-k-no"]
    if meta.get("image_url"):
        base = re.sub(r"=[^/=]*$", "", meta["image_url"])
        urls.append(f"{base}=w{w}-h{h}-k-no")
        urls.append(f"{base}=s0")
    best, err = None, None
    for u in urls:
        try:
            data = _fetch(u, tries=2)
            img = Image.open(io.BytesIO(data))
            img.load()
            if best is None or img.width * img.height > best[0].width * best[0].height:
                best = (img.convert("RGB"), data)
            if img.width >= w * 0.98 and img.height >= h * 0.98:
                break
        except requests.RequestException:
            raise
        except Exception as e:  # noqa
            err = e
    if best is None:
        raise OpenViewError(f"No se pudo descargar la foto ({friendly_error(err)})")
    return best


def thumbnail(meta, width=1024):
    """Vista previa pequeña (JPEG en bytes)."""
    if meta.get("kind") == "photo":
        fw, fh = meta["sizes"][-1]
        k = min(1.0, width / max(fw, fh))
        return _fetch(f"https://lh3.ggpht.com/jsapi2/a/b/c/w{max(1, round(fw * k))}-h{max(1, round(fh * k))}"
                      f"/{meta['id']}", tries=2)
    if is_third_party(meta["id"]):
        fw, fh = meta["sizes"][-1]
        th = max(1, round(width * fh / fw))
        return _fetch(f"https://lh3.ggpht.com/jsapi2/a/b/c/w{width}-h{th}/{meta['id']}",
                      tries=2)
    zoom = 0
    for z, (w, _) in enumerate(meta["sizes"]):
        if w <= width:
            zoom = z
    img, _ = download_official(meta, zoom, lambda *_: None)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def full_image(meta, progress=None, log=lambda *_: None):  # noqa: E302
    """Panorama completo a máxima resolución, en memoria y sin retocar (para recortes)."""
    if is_third_party(meta["id"]):
        img, _ = download_third_party(meta, log)
    else:
        img, _ = download_official(meta, len(meta["sizes"]) - 1, log, progress)
    return img


def process(link, outdir, fmt="jpg", north=False, max_width=None, log=print,
            progress=None, meta=None, save_json=False, nadir=False, fill=True, level=True):
    log(f"\n> {link[:120]}")
    if meta is None:
        meta = resolve(link, log)
    if meta.get("kind") == "photo":
        return save_photo(link, outdir, fmt, meta, log, progress, save_json)

    tp = is_third_party(meta["id"])
    zoom = len(meta["sizes"]) - 1
    if max_width:
        for z, (w, _) in enumerate(meta["sizes"]):
            if w <= max_width:
                zoom = z
    w, h = meta["sizes"][zoom]
    log(f"  Tipo: {'foto 360 de usuario' if tp else 'vista de calle oficial'}"
        f" | Fecha: {meta['date'] or '?'} | Resolución: {w} x {h}")
    if meta["uploader"]:
        log(f"  Autor: {meta['uploader']}")

    # Comprobar la carpeta antes de descargar nada (evita bajar 130 MB para nada)
    os.makedirs(outdir, exist_ok=True)

    if tp:
        img, raw = download_third_party(meta, log, zoom)
        if progress:
            progress(1.0)  # permite cancelar antes de guardar
    else:
        img, raw = download_official(meta, zoom, log, progress)

    filled = patched = leveled = False
    tilt, roll = tilt_roll(meta)
    if level and (abs(tilt) > 0.05 or abs(roll) > 0.05):
        import numpy as np
        import extras
        img = Image.fromarray(extras.level_equirect(np.asarray(img), extras.level_matrix(tilt, roll)))
        raw, leveled = None, True
        log(f"  Horizonte nivelado (inclinación {tilt:+.1f}°, alabeo {roll:+.1f}°)")
    if fill or nadir:
        import numpy as np
        import extras
        if fill and extras.is_partial(img.width, img.height):
            img = Image.fromarray(extras.fill_partial(np.asarray(img)))
            raw, filled = None, True
            log(f"  Completado hasta 2:1 ({img.width} x {img.height})")
        if nadir:
            img = Image.fromarray(extras.patch_nadir(np.asarray(img)))
            raw, patched = None, True
            log("  Suelo tapado")

    rolled = False
    if north:
        if meta["heading"] is None:
            log("  (Sin orientación en los metadatos: no se alinea al norte)")
        else:
            img = roll_to_north(img, meta["heading"])
            raw = None
            rolled = True
            log(f"  Alineado al norte (rumbo original {meta['heading']:.1f}°)")

    name = "_".join(p for p in (
        meta["date"] or "",
        f"{meta['lat']:.6f}_{meta['lon']:.6f}" if meta["lat"] is not None else "",
        safe(meta["id"])[:16],
        "norte" if rolled else "",
        "nadir" if patched else "") if p)
    ext = {"jpg": "jpg", "png": "png", "tif": "tif"}[fmt]
    path = os.path.join(outdir, f"{name}.{ext}")
    tmp = path + ".part"  # se escribe aparte y se renombra: nunca queda un archivo a medias

    log(f"  Guardando {os.path.basename(path)} ...")
    try:
        if fmt == "jpg" and raw is not None and tp and raw[:3] == b"\xff\xd8\xff":
            with open(tmp, "wb") as f:  # JPEG original, sin recomprimir
                f.write(raw)
        elif fmt == "jpg":
            img.save(tmp, "JPEG", quality=97, subsampling=0, optimize=True)
        elif fmt == "png":
            img.save(tmp, "PNG", compress_level=6)
        else:
            img.save(tmp, "TIFF", compression="tiff_lzw")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

    if save_json:
        meta_out = dict(meta, saved_width=img.width, saved_height=img.height,
                        aligned_north=bool(north and meta["heading"] is not None),
                        nadir_patched=patched, completed_to_2_1=filled, leveled=leveled,
                        source_link=link)
        with open(os.path.splitext(path)[0] + ".json", "w", encoding="utf-8") as f:
            json.dump(meta_out, f, ensure_ascii=False, indent=2)
    log(f"  OK -> {path}")
    return path


def save_photo(link, outdir, fmt, meta, log, progress=None, save_json=False):
    os.makedirs(outdir, exist_ok=True)
    img, raw = download_photo(meta, log)
    if progress:
        progress(1.0)
    name = "_".join(p for p in (
        meta["date"] or "",
        f"{meta['lat']:.6f}_{meta['lon']:.6f}" if meta["lat"] is not None else "",
        safe(meta["id"])[:16], "foto") if p)
    path = os.path.join(outdir, f"{name}.{fmt}")
    tmp = path + ".part"
    log(f"  Guardando {os.path.basename(path)} ({img.width} x {img.height}) ...")
    try:
        if fmt == "jpg" and raw[:3] == b"\xff\xd8\xff":
            with open(tmp, "wb") as f:  # el JPEG original, sin recomprimir
                f.write(raw)
        elif fmt == "jpg":
            img.save(tmp, "JPEG", quality=97, subsampling=0)
        elif fmt == "png":
            img.save(tmp, "PNG", compress_level=6)
        else:
            img.save(tmp, "TIFF", compression="tiff_lzw")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    if save_json:
        with open(os.path.splitext(path)[0] + ".json", "w", encoding="utf-8") as f:
            json.dump(dict(meta, saved_width=img.width, saved_height=img.height, source_link=link),
                      f, ensure_ascii=False, indent=2)
    log(f"  OK -> {path}")
    return path


# --------------------------------------------------------------------------
# Línea de comandos
# --------------------------------------------------------------------------
def run_batch(links, outdir, fmt, north, max_width, log, save_json=False, nadir=False, fill=True):
    ok, fail = 0, 0
    for link in links:
        try:
            process(link, outdir, fmt, north, max_width, log, save_json=save_json,
                    nadir=nadir, fill=fill)
            ok += 1
        except Exception as e:  # noqa
            fail += 1
            log(f"  ERROR: {friendly_error(e)}")
    log(f"\nTerminado: {ok} descargados, {fail} con error.")
    return fail == 0


def main():
    ap = argparse.ArgumentParser(description="OpenView - descarga panoramas 360 "
                                             "de mapas (vista de calle y fotos 360) a "
                                             "máxima resolución. Para la ventana, abre app.py.")
    ap.add_argument("enlaces", nargs="*", help="Enlaces de la aplicación de mapas o IDs de panorama")
    ap.add_argument("-o", "--salida", default=".", help="Carpeta de destino")
    ap.add_argument("-l", "--lista", help="Archivo .txt con un enlace por línea")
    ap.add_argument("-f", "--formato", choices=["jpg", "png", "tif"], default="jpg",
                    help="jpg (por defecto), png o tif (sin pérdida, mejor para editar)")
    ap.add_argument("-n", "--norte", action="store_true",
                    help="Rota la imagen para que el centro mire al norte")
    ap.add_argument("--tapar-suelo", action="store_true",
                    help="Tapa el suelo bajo la cámara (trípode, persona) con un parche de su color")
    ap.add_argument("--sin-completar", action="store_true",
                    help="No completar hasta 2:1 las fotos 360 parciales")
    ap.add_argument("-j", "--json", action="store_true",
                    help="Guarda también un .json con coordenadas, fecha, rumbo y autor")
    ap.add_argument("--ancho-max", type=int, help="Limitar el ancho (p.ej. 8192)")
    a = ap.parse_args()
    links = list(a.enlaces)
    if a.lista:
        with open(a.lista, encoding="utf-8") as f:
            links += [l.strip() for l in f if l.strip() and not l.startswith("#")]
    if not links:
        ap.print_help()
        sys.exit(2)
    ok = run_batch(links, a.salida, a.formato, a.norte, a.ancho_max, print, a.json,
                   nadir=a.tapar_suelo, fill=not a.sin_completar)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
