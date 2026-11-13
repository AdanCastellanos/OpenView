# OpenView · notes for Claude

Windows desktop app that downloads 360° panoramas (street-level imagery and user photospheres) and flat photos from a
maps application at full resolution, with a 360 viewer, perspective crops and HDRI conversion. The user is an
architect who takes the images into 3ds Max.
GitHub: `AdanCastellanos/OpenView` (public, MIT, branch `main`). The `.exe` is published in Releases, not in the code.

## Talking to the user
- Reply in **Spanish**, in plain language (the user is not a programmer).
- Repository docs (`README.md`, `CHANGELOG.md`), UI text and code comments are in **Spanish**; this file is in English.

## Layout
| path | what |
|---|---|
| `openview.py` | download engine + CLI: link parsing, metadata, tiles, user photos, `process()`, `run_batch()` |
| `extras.py` | equirectangular tools, no AI: `level_matrix` / `level_equirect`, `perspective_crop`, `patch_nadir`, `fill_partial` |
| `hdri.py` | panorama → HDRI (`.exr` via OpenEXR, `.hdr` via OpenCV), sun detection and gain; also a CLI |
| `app.py` | the app: stdlib HTTP server on `127.0.0.1` (random port, per-session token) + pywebview (WebView2) window; settings in `%APPDATA%\OpenView` |
| `ui/index.html` | the whole UI in one file (light/dark, WebGL 360 viewer, crop frame, HDRI sheet) |
| `assets/make_icon.py` | draws `icon.ico`, `logo.png`, `logo.svg` (transparent background, teal `#0f9d8a` / `#2cc2ab`) |
| `assets/hook-OpenEXR.py` | PyInstaller hook that bundles the DLL OpenEXR needs |
| `tests/test_openview.py` | unit tests |
| `build_exe.bat`, `Abrir con Python.bat` | build the `.exe`; run from source |

## Rules
- **Never write the brand names of the map service or its street-level product** in code comments, UI, docs, commit
  messages or file names. Say "aplicación de mapas", "vista de calle", "servicio de mapas". The only allowed traces
  are the technical host names and URL patterns the engine needs to work.
- Do not mention the app's former names in new text.
- **Privacy**: the app must not use the user's browser profile, accounts or sync data. The window is WebView2 with its
  own storage folder; never go back to launching Edge/Chrome in app mode.
- Rejected by the user, do not re-propose: "nearby points" search and a download history / drag-a-`.txt` feature.
- The viewer shader in `ui/index.html` and `extras.perspective_crop` share one angle convention (centre = −z, pitch
  then yaw, same levelling matrix). Change both together or the crop will not match what the viewer shows.
- Tile requests: do not add `nbt=1&fover=2` (it returns black / half-size polar tiles).
- Files are written to a `.part` temp and then renamed; keep it that way so a cancelled download leaves nothing broken.

## Working on it
- Tests: `python -m unittest discover -s tests` (set `OPENVIEW_OFFLINE=1` to skip the ones that need the network).
- Build: run `build_exe.bat` (PyInstaller, one file, windowed). The app must be closed or the `.exe` is locked.
  Then check it with `OpenView.exe --selftest report.txt` (verifies exr, hdr and the web view inside the bundle).
- `.bat` files must have CRLF line endings.
- In the browser pane use `http://localhost:<port>/`, not `127.0.0.1`.
- Not tested yet: photo ids that start with `AF1Qip`.

## Updating GitHub
Every push to GitHub includes, up to date:
1. `CHANGELOG.md`: a new entry (or additions to the "Sin publicar" section) saying what changes for the user.
2. `CLAUDE.md`: this file, updated with anything new that a future session needs to know.
3. For a release: move "Sin publicar" under the new version and date, rebuild `OpenView.exe`, and publish it with
   `gh release create vX.Y.Z OpenView.exe` (the README download link points to the latest release).

Commit as the repository's existing author name and email (`git log -1 --format='%an <%ae>'`), ending the message
with the Co-Authored-By line. Plain pushes only; ask before deleting releases or anything that rewrites history.
