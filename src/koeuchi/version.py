"""Version string assembly.

There is no manually incremented version; the Nix wrapper stamps the build
(commit date + short rev) into KOEUCHI_BUILD, and source-tree runs fall back
to "dev".
"""

from __future__ import annotations

import os
from importlib.metadata import PackageNotFoundError, version


def app_version() -> str:
    try:
        base = version("koeuchi")
    except PackageNotFoundError:
        base = "0.0.0"
    return f"{base}+{os.environ.get('KOEUCHI_BUILD', 'dev')}"
