"""
extras.py - Retoques sobre panoramas equirectangulares (sin IA):

  * perspective_crop  -> "Extraer recorte": una foto normal (perspectiva) de una parte del
                         panorama, a la máxima resolución que permite el original.
  * patch_nadir       -> "Tapar el suelo": parche con el color del suelo bajo la cámara
                         (tapa el trípode, la persona o la mancha del coche).
  * fill_partial      -> "Completar parciales": rellena cielo y suelo de un panorama que no
                         cubre la esfera completa, estirando y difuminando sus bordes, hasta 2:1.

La convención de ángulos es la misma que la del visor 360 de la app (yaw, pitch en radianes).
"""
import math

import numpy as np
from PIL import Image

Image.MAX_IMAGE_PIXELS = None
MAX_SIDE = 20000


# ---------------------------------------------------------------- nivelar
def level_matrix(tilt_deg, roll_deg):
    """Matriz que lleva una dirección del mundo (nivelado) a la del panorama tal como lo
    hizo la cámara. Convención medida con las verticales de edificios reales de la vista de calle
    (deja las verticales a ~1° de media frente a ~2° sin corregir)."""
    t, r = math.radians(-tilt_deg), math.radians(roll_deg)
    ct, st, cr, sr = math.cos(t), math.sin(t), math.cos(r), math.sin(r)
    rx = np.array([[1, 0, 0], [0, ct, -st], [0, st, ct]], np.float32)
    rz = np.array([[cr, -sr, 0], [sr, cr, 0], [0, 0, 1]], np.float32)
    return rz @ rx


def _sample(eq, dx, dy, dz, M):
    """Coordenadas del panorama para direcciones (convención del visor: centro = -z)."""
    if M is not None:
        dx, dy, dz = (M[0, 0] * dx + M[0, 1] * dy + M[0, 2] * dz,
                      M[1, 0] * dx + M[1, 1] * dy + M[1, 2] * dz,
                      M[2, 0] * dx + M[2, 1] * dy + M[2, 2] * dz)
    H, W = eq.shape[:2]
    lon = np.arctan2(dx, -dz)
    lat = np.arcsin(np.clip(dy, -1, 1))
    mu = ((lon / (2 * np.pi) + 0.5) * W - 0.5).astype(np.float32) % W
    mv = np.clip((0.5 - lat / np.pi) * H - 0.5, 0, H - 1).astype(np.float32)
    return mu, mv


def level_equirect(eq, M, step=512):
    """Endereza un panorama entero: el horizonte queda recto y las verticales, verticales."""
    import cv2
    H, W = eq.shape[:2]
    out = np.empty_like(eq)
    lon = ((np.arange(W, dtype=np.float32) + 0.5) / W - 0.5) * 2 * np.pi
    sl, cl = np.sin(lon), np.cos(lon)
    for y0 in range(0, H, step):
        y1 = min(H, y0 + step)
        lat = (0.5 - (np.arange(y0, y1, dtype=np.float32) + 0.5) / H) * np.pi
        cla, sla = np.cos(lat)[:, None], np.sin(lat)[:, None]
        dx, dy, dz = cla * sl[None], np.broadcast_to(sla, (y1 - y0, W)), -cla * cl[None]
        mu, mv = _sample(eq, dx, dy, dz, M)
        out[y0:y1] = cv2.remap(eq, mu, mv, cv2.INTER_CUBIC, borderMode=cv2.BORDER_WRAP)
    return out


# ---------------------------------------------------------------- recorte
def crop_size(src_width, fov_v_deg, aspect):
    """Tamaño del recorte con la misma densidad de píxeles que el panorama en el centro."""
    f = src_width / (2 * math.pi)                    # píxeles por radián del original
    t = math.tan(math.radians(fov_v_deg) / 2)
    h = 2 * f * t
    w = h * aspect
    k = min(1.0, MAX_SIDE / max(w, h))
    return max(16, int(round(w * k))), max(16, int(round(h * k)))


def perspective_crop(eq, yaw, pitch, fov_v_deg, aspect, size=None, M=None):
    """Proyecta una vista en perspectiva. eq: array HxWx3 uint8. Devuelve array uint8.
    M: matriz de nivelado (level_matrix) o None."""
    import cv2
    H, W = eq.shape[:2]
    w, h = size or crop_size(W, fov_v_deg, aspect)
    t = math.tan(math.radians(fov_v_deg) / 2)
    out = np.empty((h, w, eq.shape[2]), np.uint8)
    xs = (((np.arange(w, dtype=np.float32) + 0.5) / w) * 2 - 1) * t * (w / h)
    cp, sp, cy, sy = math.cos(pitch), math.sin(pitch), math.cos(yaw), math.sin(yaw)
    step = 1024                                      # por tramos: poca memoria aunque salga enorme
    for y0 in range(0, h, step):
        y1 = min(h, y0 + step)
        ys = (1 - ((np.arange(y0, y1, dtype=np.float32) + 0.5) / h) * 2) * t
        dx = np.broadcast_to(xs[None, :], (y1 - y0, w))
        dy = np.broadcast_to(ys[:, None], (y1 - y0, w))
        dz = -np.ones_like(dx)
        n = np.sqrt(dx * dx + dy * dy + 1)
        dx, dy, dz = dx / n, dy / n, dz / n
        # mismo orden que el visor: primero inclinación, luego giro
        dy, dz = dy * cp - dz * sp, dy * sp + dz * cp
        dx, dz = dx * cy + dz * sy, -dx * sy + dz * cy
        mu, mv = _sample(eq, dx, dy, dz, M)
        out[y0:y1] = cv2.remap(eq, mu, mv, cv2.INTER_CUBIC, borderMode=cv2.BORDER_WRAP)
    return out


# ---------------------------------------------------------------- nadir
def patch_nadir(eq, radius_deg=28.0):
    """Parche circular con el color del suelo que rodea el nadir (sin IA).
    La distancia al nadir solo depende de la fila, así que es muy rápido."""
    H = eq.shape[0]
    lat = (0.5 - (np.arange(H, dtype=np.float32) + 0.5) / H) * 180.0   # grados por fila
    ang = 90.0 + lat                                                   # distancia al nadir
    ring = (ang > radius_deg * 1.1) & (ang < radius_deg * 1.45)
    if not ring.any():
        return eq
    color = np.median(eq[ring].reshape(-1, eq.shape[2])[::7], axis=0).astype(np.float32)
    # borde suave
    b = np.clip((radius_deg * 1.12 - ang) / (radius_deg * 0.25), 0, 1)
    b = b * b * (3 - 2 * b)
    rows = np.nonzero(b > 0)[0]
    out = eq.copy()
    if len(rows):
        part = eq[rows].astype(np.float32)
        bb = b[rows][:, None, None]
        out[rows] = (part * (1 - bb) + color * bb + 0.5).astype(np.uint8)
    return out


# ---------------------------------------------------------------- parciales
def fill_partial(band):
    """Completa un panorama más ancho que 2:1 hasta 2:1, centrado en el horizonte,
    prolongando y difuminando la fila de arriba (cielo) y la de abajo (suelo)."""
    import cv2
    h, W = band.shape[:2]
    H = W // 2
    if h >= H:
        return band
    top = (H - h) // 2
    bot = H - h - top
    out = np.empty((H, W, band.shape[2]), np.uint8)
    out[top:top + h] = band

    def extend(edge_rows, n, towards_pole_first):
        if n <= 0:
            return np.empty((0, W, band.shape[2]), np.uint8)
        edge = edge_rows.astype(np.float32).mean(0)                         # W x C
        sharp = cv2.GaussianBlur(edge[None], (0, 0), sigmaX=3)[0]
        soft = cv2.GaussianBlur(edge[None], (0, 0), sigmaX=max(8, W / 100))[0]
        flat = np.broadcast_to(edge.mean(0), edge.shape)
        tt = np.linspace(0, 1, n, dtype=np.float32)[:, None, None]          # 0 = junto a la franja
        a = np.clip(tt * 2.5, 0, 1)
        b = np.clip(tt * 1.6 - 0.4, 0, 1)
        col = sharp * (1 - a) + soft * a
        col = col * (1 - b) + flat * b
        col = col if not towards_pole_first else col[::-1]
        return (col + 0.5).astype(np.uint8)

    out[:top] = extend(band[:4], top, True)
    out[top + h:] = extend(band[-4:], bot, False)
    return out


def is_partial(w, h):
    return abs(w / h - 2.0) > 0.02 and w / h > 2.0
