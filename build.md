# Build - C.A.P. desktop package

Packages the app into a folder and a compressed ZIP you can hand to a pilot
client. Build on the same operating system you plan to distribute for.

## 1. Install build tooling

```
pip install -r requirements-build.txt
```

## 2. Build

```
python build.py
```

Output lands in:

```
dist/CAP/                                  (dist/CAP.app on macOS)
dist/CAP-<version>-windows-x64.zip         (or -macos-arm64.zip, -linux-x64.tar.gz)
dist/CAP-<version>-windows-x64.zip.sha256  its SHA-256 checksum
```

Check the build with `dist/CAP/CAP.exe --selftest` (macOS: `dist/CAP.app/Contents/MacOS/CAP --selftest`): it
starts the app without a window, loads its pages and exits 0 on success.

To build all three systems and publish a release, push a version tag; `.github/workflows/release.yml` does the
rest. Users extract the archive and keep the `CAP/` folder (or CAP.app) intact. It is a portable application, not
a setup wizard or a single-file executable.

The build keeps PyInstaller's `onedir` layout instead of `onefile`. This avoids
extracting the full application to a temporary directory on every launch.
LiteLLM is also imported only when a user makes an AI request, rather than
during app startup. Docling is optional: it is bundled only if it is installed
in the build environment (`pip install -r requirements-optional.txt`), and it
adds well over 1 GB. Without it, PDFs and Office files are still read with
pypdf and built-in parsers. The first AI request can still take longer while Ollama
loads a model into memory.

## 3. Test the real install experience

Extract `dist/CAP.zip` on a clean machine or VM and run `CAP.exe` from the
extracted `CAP/` folder. This is where you
will see client-facing security warnings:

- Windows: SmartScreen may show a warning for an unsigned app. For a pilot,
  warn the client before they see it. A code-signing certificate removes this
  later.
- Mac: Gatekeeper may require right-click > Open the first time.

## Credentials choice

The build does not bundle `credentials.json` by default.

Option A: bundle your own OAuth desktop credentials for a hands-on pilot. This
is simpler, but all pilots use the same Google Cloud OAuth app.

Option B: create credentials per client during onboarding. This is more setup
work, but it isolates client quota and permissions.

For the first pilot, Option A is usually the smoothest path. Revisit once the
pilot turns into a broader rollout.

## Website Downloads

`dist/CAP.zip` contains a portable PyInstaller folder, not a signed installer
and not an auto-updating application. Before publishing a versioned ZIP on a
download page or release host:

- Build and test on every supported operating system and architecture.
- Test first launch, OAuth setup, upgrade behavior, data retention, and uninstall
    on a clean machine or VM.
- Confirm the package contains the `ui/` folder and no `credentials.json`, `token.json`, database,
    API key, or other user data.
- Sign Windows binaries and notarize macOS builds before a public release.
- Publish the version, supported OS requirements, installation and uninstall
    instructions, privacy/data-flow notes, release notes, and a SHA-256 checksum.

Do not describe the current folder build as enterprise-ready or compliance-
certified. A public download site should link to immutable, versioned artifacts
and their checksums; it should not host credentials or mutable `dist/` output.
