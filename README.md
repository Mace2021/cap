# C.A.P. Inbox Assistant

C.A.P. sorts your business email, flags fraud, and drafts replies that quote your own prices and policies. It runs on
your computer; there is no C.A.P. server. You review every draft and send it with one click.

**Download, setup steps and privacy details:** see the product site (GitHub Pages for this repository), or the
[latest release](../../releases/latest).

| | |
|---|---|
| Windows 10/11 (64-bit) | `CAP-<version>-windows-x64.zip`: extract, then run `CAP\CAP.exe` |
| macOS 14+ (Apple silicon) | `CAP-<version>-macos-arm64.zip`: move CAP.app to Applications |
| Linux (64-bit) | `CAP-<version>-linux-x64.tar.gz`: extract, then run `CAP/CAP` |

Each release lists SHA-256 checksums in `SHA256SUMS.txt`. Builds aren't code-signed yet.

## Developers

- Run from source: see [SETUP.md](SETUP.md).
- Package for your system: `python build.py` (see [build.md](build.md)).
- Tests: `python -m pytest -q tests`.
- Release: push a tag such as `v2.2.0`. [`.github/workflows/release.yml`](.github/workflows/release.yml) builds and
  self-tests Windows, macOS and Linux, publishes the release, and redeploys the site from [`site/`](site/).
- Changes: [CHANGELOG.md](CHANGELOG.md).
