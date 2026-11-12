# Pano360 Downloader

Descargador de panoramas 360 y fotos de aplicaciones de mapas, a **máxima resolución**:

- **Vista de calle oficial** (la del coche con cámara): normalmente 16384 × 8192 px (las antiguas, 13312 × 6656).
- **Fotos 360 de usuarios / puntos aislados** (círculos azules): a la resolución original subida.
- **Fotos normales de los sitios** (las que no dejan guardar con el botón derecho): pega el enlace de
  la foto y se descarga a su tamaño original, sin recomprimir.

El resultado es una imagen **equirectangular 2:1** estándar, que puedes retocar en cualquier editor
(recortes, quitar el cielo, ajustes de color) y luego usar en 3ds Max como mapa *Spherical Environment*,
en un *VRayDome* o *CoronaSky*, etc.

## Aplicación

Doble clic en **Pano360 Downloader.exe**. No necesita Python y puedes copiarlo a cualquier
carpeta, anclarlo a la barra de tareas o crear un acceso directo en el escritorio. Se abre en su propia ventana:

1. Pega uno o varios enlaces (basta con Ctrl+V en cualquier parte de la ventana).
2. Verás la vista previa, el tipo, la fecha, la resolución y el autor. Con **Ver en 360** puedes mirar
   alrededor para comprobar que es el panorama correcto.
3. Elige la carpeta, el formato, si quieres el centro al norte y si quieres guardar los datos (.json),
   y pulsa **Descargar**. Puedes **cancelar** una descarga, y con **Otra vez** repetirla con otras
   opciones (por ejemplo en TIFF).

### Nivelar horizonte

Las cámaras no siempre están rectas: el coche va inclinado en una cuesta o en un bache. El servicio
guarda esa inclinación y su visor la corrige; con **Nivelar horizonte** (activado por defecto)
la app hace lo mismo en el visor 360, en los recortes y en el panorama descargado, para que el
horizonte quede recto y las verticales de los edificios, verticales (lo que necesitas en 3ds Max).
Medido sobre panoramas reales, las verticales pasan de ~2° de desviación media a ~1°, y en los más
torcidos de más de 4° a 0,2°.

### Fechas anteriores

Si hay más capturas del mismo punto, la tarjeta muestra una tira con todas las fechas y su
miniatura. Pulsa una para cambiar a esa fecha antes de descargar (sin obras, en verano, sin coches…).

### Extraer recorte

Con **Extraer recorte** (en la tarjeta o dentro del visor 360) aparece un marco: elige la proporción
(16:9, 3:2, 4:3, 1:1, 4:5, 9:16), encuadra arrastrando y acerca con la rueda. Abajo ves en todo momento
el tamaño en píxeles que tendrá la foto. Al pulsar **Extraer** se descarga el panorama completo y se
saca una foto en perspectiva normal (líneas rectas) con la máxima resolución que permite el original:
cuanto más acercas, más estrecho es el campo y menos píxeles salen, porque no se inventa resolución.
Se guarda en la carpeta de destino con `_recorte` en el nombre y en el formato elegido.

### Tapar el suelo y completar parciales

- **Tapar el suelo**: tapa lo que hay justo debajo de la cámara (trípode, persona, mancha del coche) con
  un parche del color del suelo. Se guarda con `_nadir` en el nombre. Es un parche, no una
  reconstrucción: va bien si en tu escena no se ve el suelo justo bajo la cámara.
- **Completar parciales** (activado por defecto): si una foto 360 no cubre la esfera completa, rellena
  el cielo y el suelo que faltan prolongando y difuminando sus bordes hasta 2:1, centrado en el horizonte.
  Es poco frecuente: normalmente ya vienen completas.

### Crear HDRI

Pulsa **Crear HDRI** en un panorama descargado, o **Imagen a HDRI…** arriba para convertir cualquier
panorama 360 de tu equipo (por ejemplo uno que ya hayas retocado o al que hayas quitado el cielo; si el
PNG/TIFF tiene transparencia, se conserva en el EXR).

Un JPG no guarda la intensidad real del sol ni del cielo, así que la app la reconstruye sin IA:
pasa la imagen a valores lineales, devuelve brillo a cielo, nubes y reflejos, y detecta el sol para
darle la fuerza de un día real. Controles:

- **Fuerza del sol**: la luz que aporta el sol comparada con la del resto del cielo. Despejado 4–8,
  calima 1–3, nublado 0. Si no detecta el sol o se equivoca, **haz clic sobre él** en la vista previa.
- **Tamaño del sol**: más pequeño, sombras más nítidas.
- **Mirar con exposición / Ver el sol (−8 EV)**: solo cambia la vista previa; a −8 EV debe quedar
  únicamente el sol brillando.
- **Resolución** 2K/4K/8K/Original y formato **EXR** (recomendado) o **HDR**.

El HDRI se guarda junto a la imagen de origen con `_hdri` al final del nombre. Para iluminar basta 4K–8K;
como fondo nítido puedes usar el JPG de 16K.

### Otros detalles

Los panoramas alineados al norte se guardan con `_norte` al final del nombre, así no sustituyen a la
versión normal. Las fotos 360 que no cubren la esfera completa llevan la etiqueta **Parcial**.

La ventana usa WebView2, el motor web integrado en Windows: no inicia sesión con ninguna cuenta,
no sincroniza nada y no comparte historial con el navegador. Si WebView2 no estuviera disponible, se
abre en el navegador por defecto. Todo se ejecuta en tu equipo. Si cierras la ventana con descargas en
curso, te pregunta antes. La app recuerda el tamaño y la posición de la ventana, el tema claro u oscuro
y todas las opciones; se guardan en `%APPDATA%\Pano360 Downloader`.

## Archivos

| Archivo | Para qué sirve |
|---|---|
| `Pano360 Downloader.exe` | La aplicación (en *Releases*). Es lo único que necesitas para usarla |
| `pano360.py` | El motor de descarga; también se usa desde la terminal |
| `hdri.py` | El conversor a HDRI; también se usa desde la terminal |
| `extras.py` | Recortes en perspectiva, nivelado, tapar el suelo y completar parciales |
| `app.py`, `ui/index.html` | La ventana de la aplicación |
| `Abrir con Python.bat` | Abre la aplicación con Python, sin el .exe (útil si cambias el código) |
| `build_exe.bat`, `assets/` | Vuelven a crear el .exe, su icono y lo que necesita empaquetar; solo si se cambia el código |
| `tests/` | Pruebas automáticas: `python -m unittest discover -s tests -v` |
| `requirements.txt` | Librerías de Python necesarias para los scripts |

## Terminal

También puedes usarlo sin ventana:

```
python pano360.py "<enlace>" -o "<carpeta>"
python pano360.py enlace1 enlace2 -o salida -f png
python pano360.py --lista enlaces.txt -o salida
```

| Opción | Qué hace |
|---|---|
| `-o carpeta` | Carpeta de destino |
| `-f jpg/png/tif` | `jpg` por defecto (calidad 97, 4:4:4). `png`/`tif` sin pérdida, mejor si vas a editar mucho |
| `-n` | Rota la imagen para que el **centro mire al norte**; útil para orientarla en 3ds Max |
| `-l archivo.txt` | Un enlace por línea (líneas con `#` se ignoran) |
| `--tapar-suelo` | Tapa el suelo bajo la cámara con un parche |
| `--sin-completar` | No completar hasta 2:1 las fotos 360 parciales |
| `-j` | Guarda también un `.json` con coordenadas, fecha, rumbo (heading), autor y enlace original |
| `--ancho-max 8192` | Descarga una resolución menor |

El `.json` es opcional (desactivado por defecto). Solo es útil si quieres guardar dónde y cuándo se hizo
el panorama o su orientación exacta.

Y para convertir a HDRI:

```
python hdri.py panorama.jpg                      (EXR de 8K)
python hdri.py panorama.jpg --sol 8 --res 4096 --formato hdr
python hdri.py panorama.jpg --sin-sol            (día nublado)
```

## Qué enlace copiar

Abre el panorama en la aplicación de mapas (entra en la vista de calle, en la foto 360 o en la foto) y
copia la dirección de la barra del navegador. También valen los enlaces cortos del botón "Compartir".
Las fechas antiguas puedes elegirlas directamente en la app, en la tira de fechas de cada tarjeta.

Si el enlace solo tiene coordenadas (`@lat,lon`), se descarga la vista de calle más cercana (50 m).

## Notas

- El .exe funciona solo. Para usar los scripts hace falta Python 3 con las librerías de
  `requirements.txt` (`pip install -r requirements.txt`).
- Para comprobar que un .exe recién creado trae todo: `"Pano360 Downloader.exe" --selftest informe.txt`.
- Usa servicios internos de terceros que pueden cambiar sin aviso, y descargar imágenes así puede no
  estar permitido por sus condiciones de uso: úsalo para trabajo personal o de referencia y no
  redistribuyas las imágenes.

## Licencia

El código de esta aplicación se publica bajo la [licencia MIT](LICENSE): puedes usarlo, modificarlo y
distribuirlo libremente, sin garantía. La licencia cubre solo el código, no las imágenes que descargues,
que siguen siendo de sus autores y del servicio de mapas.
