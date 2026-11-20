# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

# Collect all backend submodules so dynamic/multiprocessing imports work
backend_submodules = collect_submodules("backend")

hidden_imports = sorted(
    list(
        set(
            [
                "uvicorn",
                "uvicorn.logging",
                "uvicorn.loops",
                "uvicorn.loops.auto",
                "uvicorn.protocols",
                "uvicorn.protocols.http",
                "uvicorn.protocols.http.auto",
                "uvicorn.protocols.websockets",
                "uvicorn.protocols.websockets.auto",
                "uvicorn.lifespan",
                "uvicorn.lifespan.on",
                "fastapi",
                "starlette",
                "starlette.middleware",
                "starlette.middleware.base",
                "starlette.middleware.trustedhost",
                "starlette.routing",
                "pydantic",
                "pydantic_core",
                "yaml",
                "pynput",
                "jinja2",
                "multipart",
                "qrcode",
                "qrcode.image.pil",
                "PIL",
                "PIL.Image",
            ]
            + backend_submodules
        )
    )
)

datas = [
    ("backend/app/ui/templates", "backend/app/ui/templates"),
    ("backend/app/ui/static", "backend/app/ui/static"),
    ("config/venue.yaml", "config"),
]

a = Analysis(
    ["backend/app/main.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="bumper-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
