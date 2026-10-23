"""
Packaging script -- Step 6
Builds a single-folder desktop distributable and compressed download archive.

Run with:
    python build.py

Produces (build on each system you ship for; GitHub Actions does all three):
    dist/CAP/  (or dist/CAP.app on macOS)     <- the application
    dist/CAP-<version>-windows-x64.zip        <- download for Windows
    dist/CAP-<version>-macos-<arch>.zip       <- download for macOS (the .app, made with ditto)
    dist/CAP-<version>-linux-x64.tar.gz       <- download for Linux (keeps file permissions)
    dist/<archive>.sha256                     <- its SHA-256 checksum

Why --onedir instead of --onefile:
    --onefile makes a single .exe, but it silently unpacks itself to a
    temp folder every time it launches -- slower startup, and harder to
    debug if something goes wrong on a client's machine. --onedir starts
    faster; zip compression is applied only to the downloadable artifact.

Note on credentials.json:
    This build does NOT bundle credentials.json. See build.md for why,
    and how to bundle your own once you're packaging for an actual
    client rather than testing locally.
"""

import hashlib
import importlib.util
import os
import platform
import re
import subprocess
import sys
import tarfile
from pathlib import Path
from urllib.request import urlopen
from zipfile import ZIP_DEFLATED, ZipFile

import PyInstaller.__main__


ROOT = Path(__file__).resolve().parent
BUILD_DIR = Path(os.environ.get("CAP_BUILD_DIR", ROOT / "build")).resolve()
DIST_DIR = Path(os.environ.get("CAP_DIST_DIR", ROOT / "dist")).resolve()
WORK_DIR = Path(os.environ.get("CAP_WORK_DIR", BUILD_DIR / "pyinstaller")).resolve()
SPEC_DIR = Path(os.environ.get("CAP_SPEC_DIR", BUILD_DIR / "spec")).resolve()
TOKENIZER_URL = "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken"
TOKENIZER_SHA256 = "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"
TOKENIZER_CACHE_KEY = hashlib.sha1(TOKENIZER_URL.encode("utf-8")).hexdigest()
TOKENIZER_FILE = BUILD_DIR / "tiktoken-cache" / TOKENIZER_CACHE_KEY
VERSION = re.search(r'^APP_VERSION = "([^"]+)"', (ROOT / "pipeline.py").read_text(encoding="utf-8"), re.M).group(1)
EXCLUDED_NAMES = {"credentials.json", "token.json", "triage_log.db", "user_settings.json", "ollama_settings.json"}


def _private(path):
    return path.name.casefold() in EXCLUDED_NAMES or path.suffix.casefold() in {".db", ".db-shm", ".db-wal"}


def _arch():
    machine = platform.machine().lower()
    return "arm64" if machine in {"arm64", "aarch64"} else "x64"


def create_download_archive():
    if sys.platform == "darwin":
        app_dir = DIST_DIR / "CAP.app"
        archive_path = DIST_DIR / f"CAP-{VERSION}-macos-{_arch()}.zip"
    elif sys.platform == "win32":
        app_dir = DIST_DIR / "CAP"
        archive_path = DIST_DIR / f"CAP-{VERSION}-windows-{_arch()}.zip"
    else:
        app_dir = DIST_DIR / "CAP"
        archive_path = DIST_DIR / f"CAP-{VERSION}-linux-{_arch()}.tar.gz"
    if not app_dir.is_dir():
        raise FileNotFoundError(f"PyInstaller output folder was not created: {app_dir}")
    leaked = [p for p in app_dir.rglob("*") if p.is_file() and _private(p)]
    if leaked:
        raise RuntimeError(f"Refusing to package private files: {leaked}")
    archive_path.unlink(missing_ok=True)

    if sys.platform == "darwin":
        # ditto keeps the .app bundle's symlinks, permissions and signature layout; Python's zipfile does not.
        subprocess.run(["ditto", "-c", "-k", "--keepParent", str(app_dir), str(archive_path)], check=True)
    elif sys.platform == "win32":
        with ZipFile(archive_path, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
            for path in sorted(app_dir.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(DIST_DIR).as_posix())
    else:
        with tarfile.open(archive_path, "w:gz") as archive:
            archive.add(app_dir, arcname="CAP")

    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    (archive_path.parent / f"{archive_path.name}.sha256").write_text(f"{digest}  {archive_path.name}\n", encoding="utf-8")
    return archive_path


def _iscc():
    """Inno Setup's compiler: CAP_ISCC, then the usual install folders (GitHub's Windows runners have it)."""
    candidates = [os.environ.get("CAP_ISCC", ""),
                  Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
                  Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Inno Setup 6" / "ISCC.exe",
                  Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Inno Setup 6" / "ISCC.exe"]
    return next((Path(c) for c in candidates if c and Path(c).is_file()), None)


def _sign(path):
    """Signs one file with CAP_SIGN_COMMAND (a command line with $f where the file goes), when it is set."""
    command = os.environ.get("CAP_SIGN_COMMAND")
    if command:
        subprocess.run(command.replace("$f", f'"{path}"'), shell=True, check=True)


def create_windows_installer():
    """dist/CAP-<version>-windows-x64-setup.exe: a per-user installer with Start menu entry, uninstaller and silent mode."""
    if sys.platform != "win32":
        return None
    iscc = _iscc()
    if not iscc:
        print("Inno Setup not found (install it or set CAP_ISCC); skipped the Windows installer.")
        return None
    app_dir = DIST_DIR / "CAP"
    args =[str(iscc), "/Q", f"/DAppVersion={VERSION}", f"/DSourceDir={app_dir}", f"/DOutputDir={DIST_DIR}"]
    if os.environ.get("CAP_PUBLISHER"):
        args.append(f"/DPublisher={os.environ['CAP_PUBLISHER']}")
    if os.environ.get("CAP_SIGN_COMMAND"):
        args += [f"/Scapsign={os.environ['CAP_SIGN_COMMAND']}", "/DSignTool=capsign"]
    subprocess.run([*args, str(ROOT / "installer" / "cap.iss")], check=True)
    setup = DIST_DIR / f"CAP-{VERSION}-windows-x64-setup.exe"
    digest = hashlib.sha256(setup.read_bytes()).hexdigest()
    (DIST_DIR / f"{setup.name}.sha256").write_text(f"{digest}  {setup.name}\n", encoding="utf-8")
    return setup


if "--installer-only" in sys.argv:  # repackage an existing dist/CAP without running PyInstaller again
    print(f"Windows installer created: {create_windows_installer()}")
    sys.exit(0)


def ensure_tokenizer_asset():
    if TOKENIZER_FILE.exists():
        content_hash = hashlib.sha256(TOKENIZER_FILE.read_bytes()).hexdigest()
        if content_hash == TOKENIZER_SHA256:
            return

    TOKENIZER_FILE.parent.mkdir(parents=True, exist_ok=True)
    with urlopen(TOKENIZER_URL, timeout=45) as response:
        content = response.read()
    if hashlib.sha256(content).hexdigest() != TOKENIZER_SHA256:
        raise RuntimeError("Downloaded tiktoken encoding data failed SHA-256 verification.")
    temporary_file = TOKENIZER_FILE.with_suffix(".tmp")
    temporary_file.write_bytes(content)
    temporary_file.replace(TOKENIZER_FILE)


ensure_tokenizer_asset()

PyInstaller.__main__.run([
    "launcher.py",
    "--name=CAP",
    "--onedir",
    "--windowed",       # no console window popping up on Windows/Mac
    "--noconfirm",      # overwrite dist/ without prompting each time
    *(["--osx-bundle-identifier=app.cap.inbox-assistant"] if sys.platform == "darwin" else []),
    f"--distpath={DIST_DIR}",
    f"--workpath={WORK_DIR}",
    f"--specpath={SPEC_DIR}",

    # PyInstaller's static analysis can't see these -- uvicorn picks its
    # event loop and protocol implementations at runtime, not via a
    # plain top-level import, so they have to be listed explicitly or
    # the packaged app fails immediately on launch.
    "--hidden-import=uvicorn.logging",
    "--hidden-import=uvicorn.loops.auto",
    "--hidden-import=uvicorn.protocols.http.auto",
    "--hidden-import=uvicorn.protocols.websockets.auto",
    "--hidden-import=uvicorn.lifespan.on",
    "--hidden-import=pypdf",
    "--hidden-import=tiktoken_ext.openai_public",
    f"--add-data={TOKENIZER_FILE}{os.pathsep}data-gym-cache",
    f"--add-data={ROOT / 'ui'}{os.pathsep}ui",
    f"--add-data={ROOT / 'persona.json'}{os.pathsep}.",
    # Your Microsoft app registration ID (see SETUP.md). Bundled only if you created it.
    *([f"--add-data={ROOT / 'cap_config.json'}{os.pathsep}."] if (ROOT / "cap_config.json").exists() else []),
    "--hidden-import=mail_accounts",
    "--hidden-import=facts",
    "--hidden-import=demo_packs",
    "--hidden-import=industry",
    "--hidden-import=fraud",
    "--hidden-import=tracker",
    "--collect-submodules=keyring",
    "--collect-submodules=msal",

    # litellm dynamically imports provider-specific modules depending on
    # which model you call. Collecting everything up front avoids
    # chasing "hidden import" errors one at a time after each rebuild.
    "--collect-all=litellm",
    "--collect-all=tiktoken",
    "--collect-all=tiktoken_ext",
    # Docling is optional (better PDF/scan parsing) and adds well over 1 GB.
    # It is only bundled when it is installed in the build environment.
    *([
        "--collect-all=docling",
        "--collect-all=docling_core",
    ] if importlib.util.find_spec("docling") else []),
])

if sys.platform == "win32":
    _sign(DIST_DIR / "CAP" / "CAP.exe")  # before packaging, so the ZIP and the installer both carry the signed app
print(f"Compressed download archive created: {create_download_archive()}")
installer = create_windows_installer()
if installer:
    print(f"Windows installer created: {installer}")
