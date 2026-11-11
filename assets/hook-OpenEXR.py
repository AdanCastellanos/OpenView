# Hook de PyInstaller para OpenEXR: incluye el módulo y la DLL de C++ que trae al lado.
import glob
import os
import sysconfig

site = sysconfig.get_paths()["purelib"]
binaries = [(p, ".") for p in glob.glob(os.path.join(site, "msvcp140-*.dll"))]
hiddenimports = ["Imath", "numpy"]