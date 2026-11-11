#!/usr/bin/env python3
"""
hdri.py - Convierte un panorama equirectangular de 8 bits (JPG/PNG/TIFF) en un HDRI
(.exr o .hdr) útil para iluminar en 3ds Max (VRayDome, CoronaSky/Bitmap, etc.).

Un JPG no guarda la intensidad real del sol ni del cielo (salen "quemados" a blanco),
así que aquí se reconstruye de forma creíble, sin IA:

  1. Se pasa la imagen a valores lineales (como trabaja el render).
  2. Se realzan las zonas muy claras (cielo, nubes iluminadas, reflejos) con una curva suave.
  3. Se detecta el sol (zona quemada más brillante por encima del horizonte) y se le da la
     intensidad necesaria para que la luz del sol sea N veces la del cielo (5x = día despejado).
     Así las sombras salen con la dureza y el contraste de un día real.

Uso:
    python hdri.py panorama.jpg                  -> panorama_hdri.exr (8K)
    python hdri.py panorama.jpg --sol 8 --res 4096 --formato hdr
    python hdri.py panorama.jpg --sin-sol         (día nublado)

Dependencias: numpy, Pillow, OpenEXR (para .exr), opencv-python-headless (para .hdr)
"""

import argparse
import io
import math
import os
import sys

import numpy as np
from PIL import Image, ImageFilter

Image.MAX_IMAGE_PIXELS = None

DEFAULTS = {
    "sun_ratio": 5.0,     # luz del sol / luz del cielo (0 = sin sol). Despejado ~ 4-8
    "sun_size": 1.0,      # radio del disco solar en grados (más pequeño = sombras más nítidas)
    "sun_pos": None,      # (u, v) 0..1 ; None = automático
    "highlights": 3.0,    # realce máximo de cielo y luces (1 = nada)
    "exposure": 0.0,      # EV global
    "resolution": 8192,   # ancho del HDRI ; 0 = el del original
    "format": "exr",
}

LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)


class HDRIError(Exception):
    pass


# ---------------------------------------------------------------- color
def srgb_to_linear(u8):
    x = u8.astype(np.float32) / 255.0
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4).astype(np.float32)


def linear_to_srgb_u8(lin):
    x = np.clip(lin, 0, 1)
    x = np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(x, 1 / 2.4) - 0.055)
    return (x * 255 + 0.5).astype(np.uint8)


def smoothstep(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0, 1)
    return t * t * (3 - 2 * t)


# ---------------------------------------------------------------- geometría
def direction(u, v):
    """(u, v) de la imagen equirectangular -> vector unitario. v=0 arriba (cénit)."""
    lon = (u - 0.5) * 2 * math.pi
    lat = (0.5 - v) * math.pi
    return np.array([math.cos(lat) * math.sin(lon), math.sin(lat), math.cos(lat) * math.cos(lon)],
                    dtype=np.float32)


def row_dirs(y0, y1, h, w):
    """Vectores de dirección de las filas y0..y1 (para tramos de imagen)."""
    v = (np.arange(y0, y1, dtype=np.float32) + 0.5) / h
    u = (np.arange(w, dtype=np.float32) + 0.5) / w
    lat = (0.5 - v)[:, None] * np.pi
    lon = (u - 0.5)[None, :] * 2 * np.pi
    cl = np.cos(lat)
    return cl * np.sin(lon), np.broadcast_to(np.sin(lat), (y1 - y0, w)), cl * np.cos(lon)


# ---------------------------------------------------------------- análisis
def load(path_or_img, width=None):
    img = path_or_img if isinstance(path_or_img, Image.Image) else Image.open(path_or_img)
    img.load()
    if img.width < 64 or img.height < 32:
        raise HDRIError("La imagen es demasiado pequeña.")
    if abs(img.width / img.height - 2) > 0.05:
        raise HDRIError(f"La imagen no es un panorama 360 equirectangular (2:1); "
                        f"mide {img.width} x {img.height}.")
    alpha = None
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        img = img.convert("RGBA")
        alpha = img.getchannel("A")
    img = img.convert("RGB")
    if width and width != img.width:
        img = img.resize((width, width // 2), Image.LANCZOS)
        if alpha is not None:
            alpha = alpha.resize((width, width // 2), Image.LANCZOS)
    return img, alpha


def detect_sun(lin):
    """Busca el sol: la zona quemada más brillante por encima del horizonte.
    Devuelve (u, v, confianza 0..1) o None si no hay nada que parezca el sol."""
    h, w, _ = lin.shape
    lum = lin[: h // 2] @ LUMA  # solo cielo (mitad superior)
    if lum.max() < 0.6:
        return None  # nada quemado: día nublado o sol tapado
    clipped = (lum >= 0.92 * lum.max()).astype(np.float32)
    # densidad de píxeles quemados alrededor (un reflejo suelto pesa poco; el sol, mucho)
    k = max(3, w // 128)
    dens = np.asarray(Image.fromarray((clipped * 255).astype(np.uint8)).filter(
        ImageFilter.BoxBlur(k)), dtype=np.float32) / 255
    y, x = np.unravel_index(np.argmax(dens), dens.shape)
    conf = float(min(1.0, dens[y, x] * 1.5))
    if conf < 0.25:
        return None
    # centroide de la zona quemada cercana
    r = 2 * k
    ys, xs = np.nonzero(clipped[max(0, y - r): y + r + 1, max(0, x - r): x + r + 1])
    cy = max(0, y - r) + (ys.mean() if len(ys) else r)
    cx = max(0, x - r) + (xs.mean() if len(xs) else r)
    return float((cx + 0.5) / w), float((cy + 0.5) / h), conf


def _weights(h):
    """Peso de ángulo sólido de cada fila (las filas cerca de los polos cubren menos)."""
    v = (np.arange(h, dtype=np.float32) + 0.5) / h
    return np.cos((0.5 - v) * np.pi)


def expand_highlights(lin, strength):
    """Curva suave que solo sube las zonas muy claras (L > 0.35 lineal)."""
    if strength <= 1:
        return lin
    lum = lin @ LUMA
    g = 1 + (strength - 1) * smoothstep(0.35, 1.0, lum) ** 2
    return lin * g[..., None]


def sun_mask(dirs, sun_dir, size_deg):
    dx, dy, dz = dirs
    cosang = dx * sun_dir[0] + dy * sun_dir[1] + dz * sun_dir[2]
    ang = np.degrees(np.arccos(np.clip(cosang, -1, 1)))
    return smoothstep(size_deg * 1.6, size_deg * 0.6, ang)


def solve_sun_gain(lin, sun_uv, p):
    """Ganancia del disco solar para que la luz que AÑADE el sol sea sun_ratio veces la
    luz de todo lo demás (cielo + entorno), medido sobre una superficie horizontal
    (como el suelo de la escena). Es lineal: añadido = (g - 1) * Σ L·w·m."""
    h, w, _ = lin.shape
    sd = direction(*sun_uv)
    dirs = row_dirs(0, h, h, w)
    up = np.clip(dirs[1], 0, None)                      # cos(cenit): luz sobre el suelo
    wt = _weights(h)[:, None] * up
    base = expand_highlights(lin, p["highlights"]) * (2 ** p["exposure"])
    lum = base @ LUMA
    m = sun_mask(dirs, sd, p["sun_size"]) * smoothstep(0.25, 0.8, lin @ LUMA)
    sky = float((lum * wt).sum())
    sun = float((lum * wt * m).sum())
    if sun <= 1e-9 or sd[1] <= 0.01:
        return 1.0, sky, 0.0
    return 1.0 + p["sun_ratio"] * sky / sun, sky, sun


def process_array(lin, p, sun_uv, gain, y0=0, full_h=None):
    """Aplica todo a un tramo de filas (lineal float32)."""
    h_full = full_h or lin.shape[0]
    out = expand_highlights(lin, p["highlights"])
    if sun_uv is not None and gain > 1:
        dirs = row_dirs(y0, y0 + lin.shape[0], h_full, lin.shape[1])
        m = sun_mask(dirs, direction(*sun_uv), p["sun_size"]) * smoothstep(0.25, 0.8, lin @ LUMA)
        out = out * (1 + (gain - 1) * m)[..., None]
    return out * (2 ** p["exposure"])


def analyze(img_small, params):
    """Análisis rápido sobre una copia pequeña: posición del sol y ganancia."""
    p = {**DEFAULTS, **{k: v for k, v in params.items() if v is not None}}
    lin = srgb_to_linear(np.asarray(img_small))
    auto = detect_sun(lin)
    sun_uv = tuple(p["sun_pos"]) if p.get("sun_pos") else (auto[:2] if auto else None)
    if p["sun_ratio"] <= 0:
        sun_uv_used = None
    else:
        sun_uv_used = sun_uv
    gain = 1.0
    if sun_uv_used:
        gain, _, _ = solve_sun_gain(lin, sun_uv_used, p)
    return {
        "params": p,
        "lin": lin,
        "sun_auto": auto,
        "sun_uv": sun_uv,
        "sun_used": sun_uv_used,
        "gain": gain,
    }


def describe_sun(uv):
    if not uv:
        return None
    u, v = uv
    az = ((u - 0.5) * 360) % 360   # 0 = centro de la imagen
    el = (0.5 - v) * 180
    return {"u": u, "v": v, "azimuth": round(az, 1), "elevation": round(el, 1)}


def preview(img_small, params, view_ev=0.0):
    """JPEG de vista previa + datos. view_ev baja/sube la exposición SOLO para mirar
    (con -8 EV se ve si el sol tiene intensidad de sobra)."""
    a = analyze(img_small, params)
    out = process_array(a["lin"], a["params"], a["sun_used"], a["gain"])
    peak = float(out.max())
    disp = linear_to_srgb_u8(out * (2 ** view_ev))
    buf = io.BytesIO()
    Image.fromarray(disp).save(buf, "JPEG", quality=88)
    lum = out @ LUMA
    med = float(np.median(lum[lum > 0])) if (lum > 0).any() else 1e-6
    return buf.getvalue(), {
        "sun": describe_sun(a["sun_uv"]),
        "sun_auto": bool(a["sun_auto"]),
        "sun_confidence": round(a["sun_auto"][2], 2) if a["sun_auto"] else 0,
        "sun_gain_ev": round(math.log2(a["gain"]), 1) if a["gain"] > 1 else 0,
        "peak": round(peak, 1),
        "range_ev": round(math.log2(max(peak, 1e-6) / max(med, 1e-6)), 1),
        "below_horizon": bool(a["sun_uv"] and a["sun_uv"][1] >= 0.5),
    }


# ---------------------------------------------------------------- guardar
def write_exr(path, rgb_half, alpha_half=None):
    try:
        import OpenEXR
    except ImportError as e:
        raise HDRIError(f"No se puede guardar en .exr ({e}). Prueba con el formato HDR.") from e
    channels = {"RGB": rgb_half} if alpha_half is None else {
        "RGBA": np.concatenate([rgb_half, alpha_half[..., None]], axis=2)}
    header = {"compression": OpenEXR.ZIP_COMPRESSION, "type": OpenEXR.scanlineimage}
    with OpenEXR.File(header, channels) as f:
        f.write(path)


def write_hdr(path, rgb_f32):
    try:
        import cv2
    except ImportError as e:
        raise HDRIError(f"No se puede guardar en .hdr ({e}). Prueba con el formato EXR.") from e
    if not cv2.imwrite(path, np.ascontiguousarray(rgb_f32[..., ::-1])):
        raise HDRIError("No se pudo escribir el archivo .hdr.")


def convert(src, dst=None, params=None, progress=None, cancelled=None):
    """Convierte un panorama en HDRI. Devuelve la ruta del archivo creado."""
    p = {**DEFAULTS, **{k: v for k, v in (params or {}).items() if v is not None}}
    fmt = p["format"]
    if fmt not in ("exr", "hdr"):
        raise HDRIError("Formato no válido (exr o hdr).")
    src_img = Image.open(src)
    width = int(p["resolution"]) or src_img.width
    width = min(width, src_img.width)          # no inventar resolución
    width -= width % 2
    img, alpha = load(src_img, width)
    h = img.height

    # análisis sobre copia pequeña (rápido); los ratios no dependen de la resolución
    small = img.resize((1024, 512), Image.LANCZOS) if img.width > 1024 else img
    a = analyze(small, p)

    if dst is None:
        dst = os.path.splitext(src)[0] + f"_hdri.{fmt}"
    rgb = np.empty((h, width, 3), dtype=np.float16 if fmt == "exr" else np.float32)
    src_np = np.asarray(img)
    step = 256
    for y0 in range(0, h, step):
        if cancelled and cancelled():
            raise HDRIError("Conversión cancelada.")
        y1 = min(h, y0 + step)
        lin = srgb_to_linear(src_np[y0:y1])
        rgb[y0:y1] = np.minimum(process_array(lin, p, a["sun_used"], a["gain"], y0, h), 65000)
        if progress:
            progress(0.9 * y1 / h)
    tmp = dst + ".part"
    try:
        if fmt == "exr":
            ah = None if alpha is None else (np.asarray(alpha, dtype=np.float32) / 255).astype(np.float16)
            write_exr(tmp, rgb, ah)
        else:
            write_hdr(tmp + ".hdr", rgb)
            os.replace(tmp + ".hdr", tmp)
        os.replace(tmp, dst)
    finally:
        for t in (tmp, tmp + ".hdr"):
            if os.path.exists(t):
                os.remove(t)
    if progress:
        progress(1.0)
    return dst


def main():
    ap = argparse.ArgumentParser(description="Convierte un panorama 360 (JPG/PNG/TIFF) en HDRI.")
    ap.add_argument("imagen")
    ap.add_argument("-o", "--salida", help="Archivo de salida (.exr o .hdr)")
    ap.add_argument("--formato", choices=["exr", "hdr"], default="exr")
    ap.add_argument("--res", type=int, default=8192, help="Ancho del HDRI (0 = original)")
    ap.add_argument("--sol", type=float, default=DEFAULTS["sun_ratio"],
                    help="Luz del sol / luz del cielo (5 = despejado, 0 = sin sol)")
    ap.add_argument("--sin-sol", action="store_true", help="No reforzar el sol (día nublado)")
    ap.add_argument("--tam-sol", type=float, default=DEFAULTS["sun_size"], help="Radio del sol en grados")
    ap.add_argument("--realce", type=float, default=DEFAULTS["highlights"], help="Realce de cielo y luces")
    ap.add_argument("--exposicion", type=float, default=0.0, help="EV global")
    a = ap.parse_args()
    p = {"format": a.formato, "resolution": a.res, "sun_ratio": 0 if a.sin_sol else a.sol,
         "sun_size": a.tam_sol, "highlights": a.realce, "exposure": a.exposicion}
    try:
        out = convert(a.imagen, a.salida, p, progress=lambda f: print(f"\r  {f * 100:5.1f}%", end=""))
        print(f"\nOK -> {out}")
    except (HDRIError, OSError) as e:
        print(f"\nERROR: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
