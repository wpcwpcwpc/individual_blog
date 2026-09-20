# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

# .claude/skills is bundled (PROJECT_SKILLS_DIR default). Forward slashes: a
# backslash is not a path separator on Linux, so a Windows-style entry silently
# fails to gather the directory there.
#
# .env is deliberately NOT bundled: a frozen build must not carry credentials
# inside the executable. It is read from next to the executable at runtime
# (core/config.py::_resolve_env_file).
datas = [('.claude/skills', '.claude/skills')]
binaries = []
hiddenimports = ['motor', 'pymongo', 'uvicorn.logging', 'uvicorn.loops.auto', 'uvicorn.loops.asyncio', 'uvicorn.protocols.http.auto', 'uvicorn.protocols.http.h11_impl', 'multipart', 'cryptography', 'sqlalchemy', 'frontmatter', 'aiofiles', 'httpx', 'pydantic_settings', 'python-dotenv', 'tiktoken_ext', 'tiktoken_ext.openai_public', 'openai', 'websockets', 'itsdangerous']
tmp_ret = collect_all('agno')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('beanie')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('pymilvus')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
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
    name='qa_agent',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    # console=True: this is a server — stdout/stderr carry the startup banner,
    # uvicorn access/error logs and crash tracebacks. console=False discards them.
    console=True,
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
    name='qa_agent',
)
