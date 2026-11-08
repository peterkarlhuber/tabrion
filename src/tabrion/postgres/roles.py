"""Database role checks and grants (layer A, extra ``postgres``).

Contract: ``docs/api/contract.md`` sections 4.5 and 4.6. Stub for the API freeze G-T1; the implementation follows
in TAB-016 (role_info, check_role, inspect_role) and TAB-031 (grant_runtime_role). Database roles only (owner,
runtime, backup); no application, tenant or user model (TAB-ADR-0010).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    import psycopg

__all__ = ["RoleInfo", "RoleReport", "check_role", "grant_runtime_role", "inspect_role", "role_info"]


@dataclass(frozen=True, slots=True)
class RoleInfo:
    role: str
    superuser: bool
    bypass_rls: bool
    table_owner: bool

    @property
    def restricted(self) -> bool:
        """``not (superuser or bypass_rls or table_owner)``."""
        raise NotImplementedError("tabrion.postgres.RoleInfo.restricted: implemented in TAB-016")


def role_info(
    connection: psycopg.Connection[Any],
    *,
    owner_scope: Literal["schema", "rls"] = "schema",
) -> RoleInfo:
    """The current role's privileges; ``owner_scope="schema"`` (Nodestro) or ``"rls"`` (Axentra)."""
    raise NotImplementedError("tabrion.postgres.role_info: implemented in TAB-016")


def check_role(
    connection: psycopg.Connection[Any],
    *,
    allow_superuser: bool = False,
    allow_table_owner: bool = False,
    owner_scope: Literal["schema", "rls"] = "schema",
) -> RoleInfo:
    """Start check for applications: ``RoleUnsafe`` (details ``reasons``) unless explicitly allowed."""
    raise NotImplementedError("tabrion.postgres.check_role: implemented in TAB-016")


@dataclass(frozen=True, slots=True)
class RoleReport:
    role: str
    owner: str
    """``current_user`` of the owner connection."""
    schema: str
    database: str
    tables: int
    connect: bool
    superuser: bool
    bypass_rls: bool
    owned_tables: tuple[str, ...]
    can_create: bool

    @property
    def restricted(self) -> bool:
        raise NotImplementedError("tabrion.postgres.RoleReport.restricted: implemented in TAB-016")

    def as_dict(self) -> dict[str, object]:
        """Keys of both existing formats (Nodestro dict and Axentra ``as_dict``)."""
        raise NotImplementedError("tabrion.postgres.RoleReport.as_dict: implemented in TAB-016")


def inspect_role(connection: psycopg.Connection[Any], role: str, *, schema: str | None = None) -> RoleReport:
    """Read-only report on ``role``; ``RoleMissing`` when it does not exist."""
    raise NotImplementedError("tabrion.postgres.inspect_role: implemented in TAB-016")


def grant_runtime_role(
    connection: psycopg.Connection[Any],
    role: str,
    *,
    schema: str | None = None,
    protected_tables: Sequence[str] = (),
    grant_connect: bool = True,
) -> RoleReport:
    """Grant DML-only runtime privileges in one transaction (idempotent); ``RoleMissing``, ``RoleUnsafe``.

    Needs a transaction of its own: ``UsageError("...nested_transaction")`` inside a caller's transaction.
    """
    raise NotImplementedError("tabrion.postgres.grant_runtime_role: implemented in TAB-031")
