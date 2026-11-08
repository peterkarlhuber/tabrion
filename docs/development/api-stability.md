# API stability policy

Tabrion is a required dependency of Axentra and, later, of Orkexa and Nodestro. A break in Tabrion therefore hits
several libraries and starts a cascade of changes up to Nodestro (K-34). This page is the binding policy for every
change of the public API (TAB-051; planning document 02 chapter 14, [specification section 12](../specification.md),
[ADR 0009](../adr/0009-distribution-and-versioning.md)). It applies from 0.1 on.

Tabrion is consumed through Git SHA pins; nothing is published to PyPI. "Release" below means a tag of this
repository that consumers can pin.

## What is stable

| Term | Meaning in 0.x |
|---|---|
| **stable** (S) | Name, signature and semantics change only after a minor version with a `DeprecationWarning` (S-3). Listed in `__all__` and in `docs/api/surface.json` with `"stability": "S"`. |
| **experimental** (X) | May change in any minor version. Marked in the docstring and as `X` in the [API contract](../api/contract.md#0-overview-of-the-public-names). Consumer libraries must not use it. |
| private | Everything that starts with `_`, every module that starts with `_`, and everything not in an `__all__`. |

## Rules S-1..S-8

| Rule | Content |
|---|---|
| S-1 SemVer 0.x | `0.MINOR.PATCH`. PATCH: fixes only, no change of a signature or of the behavior of a stable API. MINOR: new functions; a break of a **stable** API only after S-3. From 1.0 on normal SemVer (a break is a MAJOR version). |
| S-2 Scope | Stable are: every name in `__all__` with status S (contract section 0), the error codes (`TabrionError.code`), the fields of the returned dataclasses, the `Kind` values and the MUST rules of the internal "Levuvo Database Profile" (`conformance/SPEC.md`, TAB-050). Experimental (X) and internal names are excluded. Consumer libraries must not use X names (an import-linter list in Axentra, Orkexa and Nodestro checks that). |
| S-3 Deprecation window | A stable name is first marked with a `DeprecationWarning` that names its replacement and the version of its removal. The removal follows at the earliest in the minor version **after next** (deprecated in 0.2, removed in 0.4 at the earliest), and at least **8 weeks** after the deprecating release. Consumer CI runs with `-W error::DeprecationWarning` and notices the deprecation at once. |
| S-4 Additive changes | New optional keyword parameters, new dataclass fields at the end, new `Kind` values (consumers must handle `UNKNOWN`) and new error codes are **not** breaks. |
| S-5 Behavior changes | Security fixes may tighten behavior in a PATCH version, for example a stricter redaction. They are listed under `### Security` in the CHANGELOG, with a notice to every consumer. |
| S-6 Check | `scripts/api_surface.py` (TAB-023) compares `__all__`, signatures and dataclass fields with `docs/api/surface.json`, and every deprecation with the CHANGELOG. With `--base <ref>` it also requires that every surface change since `<ref>` is named in the matching `[Unreleased]` section (`Deprecated`, `Removed`, `Changed`). The golden files (TAB-022a) secure the behavior of the existing functions. |
| S-7 Downstream early warning | The nightly job `downstream` (TAB-022b) runs the database tests of Axentra, Orkexa[postgres] and Nodestro against Tabrion HEAD. A red run blocks the next Tabrion release. |
| S-8 Cascade on a break | (1) A Tabrion minor version with the deprecation and the replacement. (2) The consumers switch **before** the removal appears, in the order of the edge list: orkexa and axentra, then nodestro-runtime, api, sdk, studio. (3) The removal follows at the earliest after S-3, and only when every consumer's `master` has switched (the `downstream` job is green without deprecation warnings). The version ranges or pins of the libraries move in one coordinated set of pull requests. |

## Deprecating a stable name

All steps happen in one pull request.

1. **Wrap the name with the internal helper `tabrion._deprecated`.** It is not public: it has an empty `__all__`
   and no entry in `surface.json` (contract I-80).

    ```python
    from tabrion._deprecated import deprecated_callable


    @deprecated_callable("tabrion.sql.split", replacement="tabrion.sql.split_statements", since="0.2", remove_in="0.4")
    def split(sql: str) -> list[str]:
        return split_statements(sql)
    ```

    A module-level name that moved leaves the module namespace and is served by a PEP 562 `__getattr__`:

    ```python
    from tabrion._deprecated import module_getattr, register

    _OLD = register("tabrion.sqlite.OldName", replacement="tabrion.sqlite.NewName", since="0.2", remove_in="0.4")
    __getattr__ = module_getattr(__name__, {"OldName": (_OLD, lambda: NewName)})
    ```

    Every use then warns, pointing at the caller's line:

    ```text
    DeprecationWarning: tabrion.sql.split is deprecated since 0.2 and will be removed in 0.4;
    use tabrion.sql.split_statements instead
    ```

    `register` refuses a removal version earlier than S-3 allows (`0.2` to `0.3`, or `1.3` to `1.5`), and a name
    registered twice. `functools.wraps` keeps the signature, so `surface.json` does not change for the wrapped
    function. The name stays in `__all__` until its removal.

2. **Record it in `docs/api/surface.json`:** run `python scripts/api_surface.py --write`. It adds
   `"deprecated": {"replacement": ..., "since": ..., "remove_in": ...}` to the name's entry. The check fails when the
   code and the file differ, when `surface.json` marks a name the code does not warn for, and once the package
   version reaches `remove_in`.
3. **Name it under `### Deprecated`** in the `[Unreleased]` section of `CHANGELOG.md`, with its full path in
   backticks (`` `tabrion.sql.split` `` or `` `sql.split` ``), the replacement and the removal version. The check
   fails without that entry.
4. **Update the contract** (`docs/api/contract.md`): the name's section gets a "Deprecated since" note.

## Removing a deprecated name

Only after S-3 (the removal version is reached, 8 weeks have passed) and S-8 (every consumer has switched):

1. delete the name, its `register` or decorator call and its `surface.json` entry;
2. name it under `### Removed` in the CHANGELOG;
3. update the contract.

## Release flow: notifying downstream

Every release that deprecates, removes or tightens something (S-3, S-5, S-8) notifies the consumers before the tag:

1. Run `python scripts/api_surface.py --base <previous release tag>`: every surface change since the previous release
   is in the CHANGELOG.
2. Check the `downstream` nightly (S-7) on the release commit: green, and without deprecation warnings when the
   release removes a name.
3. Open one issue in each consumer repository (axentra, orkexa, nodestro-runtime; then nodestro-api, nodestro-sdk,
   tabrion-studio where they use the name) titled `Tabrion <version>: deprecations and removals`, listing each
   `Deprecated`, `Removed` and `Security` entry with its replacement and removal version, and linking the CHANGELOG.
4. Tag only after steps 1 to 3. No release is public without its own owner approval (G-R).

The automation of steps 1 and 3 in the workflows (`ci.yml` job `api surface` with `--base`, a step in `release.yml`)
is recorded in [TODO.md](../TODO.md) (TB-090); until then the release checklist runs them by hand.
