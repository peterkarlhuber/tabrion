# Profile files and schema fingerprints (0.2)

Two small building blocks for tools that hand databases to each other, such as Tabrion Studio and Nodestro
(change request CR-DB): a **connection profile file format** that holds no secret, and a **schema fingerprint**
that tells whether a database still has the tables a release expects. Both are experimental in 0.2. Contract:
[section 5.9](../api/contract.md#59-connection-profile-files-tabrionprofilefile-since-02-experimental-tab-cr-1)
and [section 5.10](../api/contract.md#510-schema-fingerprint-tabrionschema-since-02-experimental-extra-sqlalchemy-tab-cr-2).

## Connection profile files

`tabrion.profilefile` reads, checks and writes the format (TOML, `version = 1`, one `[[profile]]` table per
database). It stores nothing and never resolves a secret: `password` is a reference that the consumer resolves.

```pycon
>>> from tabrion import profilefile
>>> text = """
... version = 1
...
... [[profile]]
... name = "invoices"
... engine = "postgresql"
... host = "db.example.internal"
... database = "billing"
... user = "invoices_ro"
... password = "env:DB_INVOICES_PASSWORD"
... environment = "prod"
... allow = ["public.invoices", "public.invoices.total_cents"]
... """
>>> profile = profilefile.parse(text).get("invoices")
>>> profile.password
SecretRef(kind='env', target='DB_INVOICES_PASSWORD')
>>> profile.mode, profile.sslmode, profile.port
('read_only', 'verify-full', 5432)
>>> profile.url()
'postgresql+psycopg://invoices_ro@db.example.internal:5432/billing?sslmode=verify-full'
```

The secret reference is one of `env:NAME`, `keyring:` (the consumer's default entry), `keyring:<service>/<user>`,
`file:PATH` and `prompt`. A plain password is refused, and the error never repeats it: every issue is a key path
and a code.

```pycon
>>> try:
...     profilefile.parse(text.replace("env:DB_INVOICES_PASSWORD", "s3cr3t"))
... except profilefile.ProfileFileError as exc:
...     print(exc.code, [(issue.pointer, issue.code) for issue in exc.issues])
profilefile.invalid [('profile[0].password', 'profilefile.plain_password')]
```

Defaults: `mode` is `read_only`; `sslmode` is `prefer` for a socket folder, `localhost` and a loopback address and
`verify-full` for any other host. A path or `file:` target on another host or a device (`//host/share`,
`\\host\share`, `\??\UNC\host\share`, `/net/host`, `C:x`) is refused. Unknown keys are warnings, so a newer writer's file still reads. `dump` writes the canonical form and
always reads back to the same profiles:

```pycon
>>> written = profilefile.dump([profile])
>>> profilefile.parse(written).profiles == (profile,)
True
>>> print(written.replace("\n\n", "\n"), end="")
version = 1
[[profile]]
name = "invoices"
engine = "postgresql"
host = "db.example.internal"
port = 5432
database = "billing"
user = "invoices_ro"
password = "env:DB_INVOICES_PASSWORD"
sslmode = "verify-full"
environment = "prod"
mode = "read_only"
allow = ["public.invoices", "public.invoices.total_cents"]
```

`profilefile.load(path)` reads a file and refuses a symbolic link, a file that is not regular and one larger than
256 KiB, because a profile file may come from a repository you do not trust. Such a file names both the host and
where the secret comes from, so a consumer resolves its references only after the user has confirmed the host and
the reference, and requires `require_auth=scram-sha-256` and `sslmode` `verify-ca` or `verify-full` for a remote host
it did not configure itself (contract I-109 j). Tabrion Studio's `studio.toml` (schema
version 1) is a valid profile file.

## Schema fingerprints

`tabrion.schema` (extra `sqlalchemy`) takes a snapshot of some tables through `Database.inspector()`: column names,
types and nullability, and the primary key, never data. `fingerprint` hashes it; `compare` names each difference.

```pycon
>>> from tabrion import Database, schema
>>> db = Database.from_url("sqlite:///billing.db")
>>> db.execute("CREATE TABLE invoices (id INTEGER PRIMARY KEY, total_cents INTEGER NOT NULL)")
0
>>> with db.inspector() as inspector:
...     released = schema.snapshot(inspector, tables=["invoices"])
>>> schema.fingerprint(released).startswith("v1:sha256:")
True
>>> stored = released.to_json()  # kept with the release, for clear messages later
>>> db.execute("ALTER TABLE invoices DROP COLUMN total_cents")
0
>>> with db.inspector() as inspector:
...     now = schema.snapshot(inspector, tables=["invoices"])
>>> schema.fingerprint(now) == schema.fingerprint(released)
False
>>> [(str(d), d.breaking) for d in schema.compare(schema.SchemaSnapshot.from_json(stored), now)]
[('column_missing: main.invoices.total_cents', True)]
>>> db.close()
```

An added table or column and a new column order (`column_order`) are not breaking (`breaking` is `False`); a
missing table or column, a changed type or nullability and a changed primary key are. Views are left out. The type text comes from SQLAlchemy's dialect compiler, so a SQLAlchemy
upgrade can change a fingerprint without a schema change; `compare` then names the column, and the consumer records
the new fingerprint.
