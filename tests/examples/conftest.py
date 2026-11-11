"""The reference applications under ``examples/`` run inside the normal suite (TODO TB-010: a dedicated job later).

Each application directory (``examples/<name>``, holding the package ``<name>``, with ``_`` for ``-``) is put on
``sys.path``; ``mypy_path`` in ``pyproject.toml`` names the same directories for ``mypy --strict src tests
scripts``. A tree without ``examples/`` (the sdist ships ``tests/`` only) does not collect ``test_<name>.py`` and
``test_<name>_*.py`` (again ``_`` for ``-``; review L-4 of #16); CI always runs from the checkout, where every
application is present.
"""

from __future__ import annotations

from pathlib import Path
import sys

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
APPLICATIONS = ("measurements", "parts", "parts-baseline")

collect_ignore: list[str] = []
collect_ignore_glob: list[str] = []
for application in APPLICATIONS:
    directory = EXAMPLES / application
    if directory.is_dir():
        if str(directory) not in sys.path:
            sys.path.insert(0, str(directory))
    else:
        module = application.replace("-", "_")
        collect_ignore.append(f"test_{module}.py")
        collect_ignore_glob.append(f"test_{module}_*.py")
