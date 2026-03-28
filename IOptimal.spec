# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = ['pystray._win32', 'PIL', 'uvicorn.logging', 'uvicorn.protocols.http', 'uvicorn.protocols.http.auto', 'uvicorn.protocols.http.h11_impl', 'uvicorn.protocols.http.httptools_impl', 'uvicorn.protocols.websockets', 'uvicorn.protocols.websockets.auto', 'uvicorn.lifespan', 'uvicorn.lifespan.on', 'uvicorn.lifespan.off', 'aiosqlite', 'sqlalchemy.dialects.sqlite', 'sqlalchemy.dialects.sqlite.aiosqlite']
hiddenimports += collect_submodules('watchdog')
hiddenimports += collect_submodules('pystray')


a = Analysis(
    ['desktop\\app.py'],
    pathex=[],
    binaries=[],
    datas=[('data/aeromaps_parsed', 'data/aeromaps_parsed'), ('data/cars', 'data/cars'), ('data/tracks', 'data/tracks'), ('webapp/templates', 'webapp/templates'), ('webapp/static', 'webapp/static'), ('skill', 'skill')],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='IOptimal',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='IOptimal',
)
