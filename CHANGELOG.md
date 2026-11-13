# Cambios

Formato: una sección por versión, la más reciente arriba. Cada entrada dice qué cambia para quien usa la aplicación.

## Sin publicar

- La licencia MIT va a nombre de Daniel Castellanos.
- Se añaden este registro de cambios y `CLAUDE.md` al repositorio.

## 1.1.0 · 2026-11-12

- La aplicación pasa a llamarse **OpenView**. El ejecutable es `OpenView.exe` y los ajustes se guardan en
  `%APPDATA%\OpenView`.
- Logo nuevo con fondo transparente (misma paleta verde azulada), legible sobre fondos claros y oscuros.
- Licencia MIT.

## 1.0.0 · 2026-11-11

Primera versión publicada.

- Descarga panoramas 360° de la vista de calle y fotos 360° de usuarios a la máxima resolución, pegando el enlace
  de la aplicación de mapas. Varios enlaces a la vez, con progreso y cancelación.
- Descarga también las fotos normales (no 360°) a tamaño completo.
- **Fechas anteriores**: elige entre las capturas históricas del mismo punto.
- **Nivelar horizonte**: endereza el panorama para que las verticales queden verticales.
- **Orientar al norte**, **Tapar el suelo** (parche bajo la cámara) y **Completar parciales** (rellena cielo y suelo
  de los panoramas que no cubren la esfera entera).
- **Visor 360°** integrado y **Extraer recorte**: una foto en perspectiva de la vista elegida, a la máxima resolución
  que permite el original.
- **Conversión a HDRI** (`.exr` o `.hdr`) con detección del sol y control de su intensidad, tamaño, realce de luces
  y exposición.
- Guarda en JPG, PNG o TIFF, con `.json` de metadatos opcional.
- Tema claro y oscuro; recuerda el tamaño de la ventana y los ajustes.
- Un solo `.exe` para Windows, sin instalación. No usa cuentas ni envía datos del usuario.
- También se puede usar desde la línea de comandos (`openview.py`, `hdri.py`).
