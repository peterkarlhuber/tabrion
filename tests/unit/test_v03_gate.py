"""The 0.3 gate of TAB-XW-6 after the 0.3 switch (ADR 0015 Consequences, as TAB-042 for 0.2; ``tabrion._since``).

Until master built as 0.3, ``Transaction.execute_checked``, ``tabrion.errors.RowCountMismatch`` and
``tabrion.rowedit`` were unimportable (the G-T1 C-6 mechanism, proven in both states here). Since ``0.3.0.dev0`` they
are public without the gate: ``Transaction`` inherits the mixin of ``tabrion._sa._v03``, ``tabrion.errors`` binds the
error and ``tabrion.rowedit`` imports, whatever ``tabrion._version.__version__`` says. These tests prove that state,
end to end in a fresh interpreter, and that the version leaves the consumers' range ``tabrion>=0.2,<0.3``.

The gate itself (:func:`tabrion._since.released`) stays for a name planned for a later minor than the package's; its
check in ``scripts/api_surface.py`` is probed by ``tests/unit/test_api_surface.py``
(``test_a_name_of_a_later_minor_must_not_be_importable``).
"""

from __future__ import annotations

from collections.abc import Iterator
import importlib
import os
import subprocess
import sys

import pytest

import tabrion._since
import tabrion._version
from tests.conftest import SRC

pytest.importorskip("sqlalchemy")


@pytest.fixture
def fresh_rowedit(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """``tabrion.rowedit`` is imported anew by the test, and the earlier module is back afterwards."""
    before = sys.modules.get("tabrion.rowedit")
    monkeypatch.delitem(sys.modules, "tabrion.rowedit", raising=False)
    yield
    if before is not None:
        tabrion.rowedit = before
    else:
        sys.modules.pop("tabrion.rowedit", None)
        if hasattr(tabrion, "rowedit"):
            delattr(tabrion, "rowedit")


@pytest.mark.parametrize(
    ("version", "expected"),
    [("0.2.2.dev0", False), ("0.2.9", False), ("0.3.0.dev0", True), ("0.3.0", True), ("0.4.1", True), ("1.0.0", True)],
)
def test_released_reads_the_version_when_called(monkeypatch: pytest.MonkeyPatch, version: str, expected: bool) -> None:
    monkeypatch.setattr(tabrion._version, "__version__", version)
    assert tabrion._since.released("0.3") is expected


def test_released_refuses_a_text_that_is_no_version() -> None:
    with pytest.raises(ValueError, match="not a version"):
        tabrion._since.released("next")


def test_the_package_is_at_least_0_3() -> None:
    assert tabrion._since.minor(tabrion._version.__version__) >= (0, 3)
    assert tabrion._since.released("0.3")


def test_the_version_is_outside_the_consumers_0_2_range() -> None:
    """The consumers require ``tabrion>=0.2,<0.3``; a 0.3 development version must not satisfy it (PEP 440: ``<0.3``
    excludes the pre-releases of 0.3, ``0.3.0.dev0`` among them), so no consumer resolves to this master by range."""
    specifiers = pytest.importorskip("packaging.specifiers")
    version = pytest.importorskip("packaging.version")
    current = version.Version(tabrion._version.__version__)
    consumers = specifiers.SpecifierSet(">=0.2,<0.3")
    assert not consumers.contains(current, prereleases=True)
    assert not consumers.contains(current)
    assert consumers.contains("0.2.1") and consumers.contains("0.2.2")


@pytest.mark.usefixtures("fresh_rowedit")
@pytest.mark.parametrize("version", ["0.2.9", "0.3.0.dev0"])
def test_the_0_3_names_need_no_gate_any_more(monkeypatch: pytest.MonkeyPatch, version: str) -> None:
    """``tabrion.rowedit`` imports whatever the version says: the 0.3 switch removed its import gate."""
    monkeypatch.setattr(tabrion._version, "__version__", version)
    module = importlib.import_module("tabrion.rowedit")
    staged = importlib.import_module("tabrion._sa._rowedit")
    assert module.__all__ == ["delete_row", "insert_row", "update_row"]
    for name in module.__all__:
        assert getattr(module, name) is getattr(staged, name)


def test_row_count_mismatch_is_exported() -> None:
    import tabrion.errors

    assert "RowCountMismatch" in tabrion.errors.__all__
    assert issubclass(tabrion.errors.RowCountMismatch, tabrion.errors.TabrionError)
    assert tabrion.errors.RowCountMismatch.__qualname__ == "RowCountMismatch"


PROBE = """
import tabrion, tabrion.errors, tabrion.rowedit as rowedit
import sqlalchemy
print(tabrion.__version__ == tabrion._version.__version__)
print(hasattr(tabrion.Transaction, "execute_checked"), "RowCountMismatch" in tabrion.errors.__all__)
meta = sqlalchemy.MetaData()
t = sqlalchemy.Table("t", meta, sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True),
                     sqlalchemy.Column("v", sqlalchemy.String))
with tabrion.Database.from_url("sqlite://") as db:
    with db.transaction() as tx:
        meta.create_all(tx.connection)
        tx.execute_checked(rowedit.insert_row(t, {"id": 1, "v": "a"}), expect_rowcount=1)
    try:
        with db.transaction() as tx:
            tx.execute_checked(rowedit.update_row(t, {"id": 1}, old={"v": "b"}, new={"v": "c"}), expect_rowcount=1)
    except tabrion.errors.RowCountMismatch as exc:
        print("conflict:", exc.code, dict(exc.details))
"""


def test_the_0_3_names_are_public_in_a_fresh_interpreter() -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    env["PYTHONPATH"] = str(SRC)
    result = subprocess.run(
        [sys.executable, "-c", PROBE], env=env, capture_output=True, text=True, timeout=120, check=False, cwd=SRC
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "True",
        "True True",
        "conflict: transaction.rowcount_mismatch {'expected': 1, 'actual': 0}",
    ]
