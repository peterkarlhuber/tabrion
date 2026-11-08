"""Connection-URL helpers (layer A, standard library only).

Contract: ``docs/api/contract.md`` section 2.3. Stub for the API freeze G-T1; the implementation follows in TAB-012.
"""

from __future__ import annotations

from collections.abc import Mapping

__all__ = ["safe_url", "url_from_env"]


def safe_url(url: str) -> str:
    """Mask the password of a URL or keyword conninfo (``postgresql://u:***@h/db``, ``password=***``).

    Query parameters named ``password`` are masked as well. An unparseable input returns ``"<unparseable url>"``.
    Never raises (TAB-T-C01..C08).
    """
    raise NotImplementedError("tabrion.config.safe_url: implemented in TAB-012")


def url_from_env(name: str, *, environ: Mapping[str, str] | None = None, required: bool = True) -> str | None:
    """Read a URL from ``name`` or from the file named by ``name + "_FILE"``.

    A file must be a regular file (symlinks allowed); its content without the trailing newline counts. Both set:
    ``ConfigurationError("config.ambiguous")``; neither set and ``required``: ``ConfigurationError("config.missing")``.
    No message contains a value.
    """
    raise NotImplementedError("tabrion.config.url_from_env: implemented in TAB-012")
