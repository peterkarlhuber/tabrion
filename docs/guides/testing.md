# Testing with `tabrion.testing`

`tabrion.testing` is a pytest plugin with fixtures for SQLite files, throwaway PostgreSQL schemas, databases and
roles, and two sets of reusable test cases: the SQLite profile rules and the data-type portability rules. It needs
the extra `testing` (`tabrion[testing]`, pytest 8 or later); the PostgreSQL fixtures also need `tabrion[postgres]`.
Contract: [section 6](../api/contract.md#6-tabriontesting-extra-testing). Tabrion's own suite runs on these fixtures.

## Activating the plugin

The plugin is never loaded on its own (there is no `pytest11` entry point). Activate it in your `conftest.py`:

<!-- docs-test: file=conftest.py -->
```python
pytest_plugins = ["tabrion.testing"]


def pytest_configure(config):
    # Optional: read the URL from your own variable names instead of TABRION_TEST_POSTGRES.
    from tabrion.testing import configure

    configure(env_prefix="TABRION")
```

Import `tabrion.testing` inside `pytest_configure`, not at the top of the root `conftest.py`: a module imported before
pytest registers it as a plugin cannot get pytest's assertion rewriting, and pytest warns about it
(`PytestAssertRewriteWarning`), which fails a suite that turns warnings into errors.

`configure` chooses the environment variables (`<prefix>_TEST_POSTGRES`, `<prefix>_REQUIRE_POSTGRES`,
`<prefix>_TEST_RUNTIME_ROLE`, `<prefix>_REQUIRE_EXTENSIONS`) and the `schema_prefix` (default `t_`) of every schema,
role and database the fixtures create. `configure(postgres_env=("MYAPP_DATABASE_URL",))` names a variable directly.

## SQLite fixtures

`tabrion_sqlite_path` is a path in `tmp_path` (not created); `tabrion_sqlite` is a connection from
`tabrion.sqlite.connect` on it, closed after the test.

<!-- docs-test: file=test_sqlite_store.py -->
```python
from tabrion import sqlite


def test_insert(tabrion_sqlite):
    with sqlite.write_transaction(tabrion_sqlite):
        tabrion_sqlite.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        tabrion_sqlite.execute("INSERT INTO t (v) VALUES ('x')")
    assert tabrion_sqlite.execute("SELECT count(*) FROM t").fetchone() == (1,)


def test_path_is_fresh(tabrion_sqlite_path):
    assert not tabrion_sqlite_path.exists()
```

## PostgreSQL fixtures

They read a superuser or owner conninfo from `TABRION_TEST_POSTGRES` (after `configure`, your variable). Without it,
every test that uses them is **skipped**; with `TABRION_REQUIRE_POSTGRES=1` a missing server is an error instead, which
is what CI should set.

| Fixture | Scope | Gives |
|---|---|---|
| `tabrion_postgres_url` | session | the URL from the environment |
| `tabrion_postgres_schema` | function | `PostgresSchema(name, owner_url, runtime_url)`: a fresh schema `t_<hex>`, its URL with that `search_path`, dropped after the test; `runtime_url` is set in runtime-role mode (`TABRION_TEST_RUNTIME_ROLE=1`), a LOGIN role without privileges plus `grant_runtime_role` |
| `tabrion_postgres_database` | session | `PostgresDatabaseHandle(url, name, owner_url, app_url)`: a database of its own (`CREATEDB` needed), dropped at the end, with an owner and an app role without superuser |
| `tabrion_role_setup` | session | the `RoleSetup` for `tabrion_postgres_database`; override it to name your roles |
| `tabrion_postgres_roles` | session | `TestRoles(owner, app, owner_url, app_url)` in the existing database |
| `postgres_server_version` | session | `server_version_num` as `int` |

Helpers: `truncate_tables(conn_or_url, *, schema=None, like=None, exclude=())` empties the schema's tables in one
`TRUNCATE ... RESTART IDENTITY CASCADE` (it refuses `public` and other schemas the plugin did not create unless you
name them); `require_extension(conn, name)` creates an extension or skips the test (an error when the name is listed
in `TABRION_REQUIRE_EXTENSIONS`).

<!-- docs-test: file=test_postgres_store.py -->
```python
import psycopg


def test_schema_is_empty(tabrion_postgres_schema):
    with psycopg.connect(tabrion_postgres_schema.owner_url, autocommit=True) as conn:
        assert conn.execute("SELECT current_schema()").fetchone() == (tabrion_postgres_schema.name,)
        conn.execute("CREATE TABLE parts (id int PRIMARY KEY)")
```

Overriding the roles of the database fixture in `conftest.py`:

```python
import pytest

from tabrion.testing import RoleSetup


@pytest.fixture(scope="session")
def tabrion_role_setup() -> RoleSetup | None:
    return RoleSetup(app="inventory_app", owner="inventory_owner")
```

Every role the fixtures create is `NOSUPERUSER NOBYPASSRLS` with a random password, so your tests run with the
privileges production has. The URL role needs `CREATEROLE` (and `CREATEDB` for the database fixture), not superuser.

## The profile cases: does your connect function keep the rules?

If your application opens SQLite itself, `profiles.sqlite_profile_cases()` checks that its connect function keeps the
rules of the Tabrion profile: foreign keys on (F01), WAL (F02), a busy timeout of at least one second (F03), private
file modes (F04, POSIX), refused symlinks (F05) and writes that do not begin deferred (F06). Each case takes your
connect function and an empty directory and raises `AssertionError` on a violation.

```pycon
>>> import sqlite3
>>> from pathlib import Path
>>> from tabrion import sqlite
>>> from tabrion.testing import profiles
>>> [case.id for case in profiles.sqlite_profile_cases()]
['TAB-T-F01', 'TAB-T-F02', 'TAB-T-F03', 'TAB-T-F04', 'TAB-T-F05', 'TAB-T-F06']
>>> for i, case in enumerate(profiles.sqlite_profile_cases()):
...     directory = Path(f"profile-{i}")
...     directory.mkdir()
...     case.check(lambda path: sqlite.connect(path), directory)
>>> naive = profiles.sqlite_profile_cases()[0]
>>> Path("naive").mkdir()
>>> naive.check(lambda path: sqlite3.connect(path), Path("naive"))
Traceback (most recent call last):
  ...
AssertionError: ...
```

In a test suite, parametrize over them:

<!-- docs-test: file=test_profile_rules.py -->
```python
import pytest

from tabrion import sqlite
from tabrion.testing import profiles


def my_connect(path):
    return sqlite.connect(path)  # the application's own connect function


@pytest.mark.parametrize("case", profiles.sqlite_profile_cases(), ids=lambda case: case.id)
def test_profile(case, tmp_path):
    case.check(my_connect, tmp_path)
```

## The portability cases

`profiles.portability_cases()` checks the data-type rules of [Portability](portability.md) (TAB-T-D01..D09) against
an open `tabrion.Database`, on SQLite and on PostgreSQL. Each case creates its own tables under fresh names and drops
them again; run them in an empty or dedicated schema.

<!-- docs-test: file=test_portability.py -->
```python
import pytest

from tabrion import Database
from tabrion.testing import profiles


@pytest.fixture
def database(tmp_path):
    with Database.from_url(f"sqlite:///{tmp_path / 'portability.db'}") as db:
        yield db


@pytest.mark.parametrize("case", profiles.portability_cases(), ids=lambda case: case.id)
def test_portability(case, database):
    case.check(database)
```

The same parametrization with a `Database` on `tabrion_postgres_schema` runs them on PostgreSQL. The fixture URLs
are libpq conninfo strings (`host=... dbname=...`), which psycopg takes as they are; `Database.from_url` needs a
`postgresql://` URL, so convert them first (Tabrion's own `tests/postgres/test_guides_postgres.py` shows one way).

## Running it

```bash
pytest
TABRION_TEST_POSTGRES="host=localhost user=postgres dbname=postgres" TABRION_REQUIRE_POSTGRES=1 pytest
```

<!-- docs-test: pytest -->

```pycon
>>> sorted(p.name for p in Path(".").glob("test_*.py"))
['test_portability.py', 'test_postgres_store.py', 'test_profile_rules.py', 'test_sqlite_store.py']
```
