# Bundled runtime dependencies

This directory contains the pure-Python runtime bundle used by the optional
`youtube-transcript-api==1.2.4` adapter. The hook never installs or downloads
these packages; `hooks/launcher.sh` places this directory first on
`PYTHONPATH` for plugin processes.

Every shipped bundle file except this manifest is listed and hash-verified by
`MANIFEST.sha256`. The upstream wheel hashes used to produce the bundle are
kept separately in `SOURCES.sha256`; the wheel archives are not shipped. Exact
upstream license texts are retained in `LICENSES/` and in each package's
`.dist-info` directory. No upstream NOTICE file was present in the bundled
wheels. Upstream test assets and non-runtime credential-looking proxy examples
are omitted or sanitized.

| Package | Version | License | Upstream |
|---|---:|---|---|
| youtube-transcript-api | 1.2.4 | MIT | https://github.com/jdepoix/youtube-transcript-api |
| defusedxml | 0.7.1 | Python Software Foundation License | https://github.com/tiran/defusedxml |
| requests | 2.32.5 | Apache-2.0 | https://github.com/psf/requests |
| charset-normalizer | 3.5.1 | MIT | https://github.com/jawah/charset_normalizer |
| idna | 3.20 | BSD-3-Clause | https://github.com/kjd/idna |
| urllib3 | 1.26.20 | MIT | https://github.com/urllib3/urllib3 |
| certifi | 2026.7.22 | MPL-2.0 | https://github.com/certifi/python-certifi |

Optional extras from `requests` and `urllib3` are intentionally not bundled;
the adapter does not use SOCKS, Brotli, HTTP/2, or Zstandard extras.
