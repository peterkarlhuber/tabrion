# Connection URLs and secrets

`tabrion.config` has two small functions for the place where most credential leaks start: printing a connection
string, and reading it from the environment. Both need only the standard library. Contract:
[section 2.3](../api/contract.md#23-safe_url-url_from_env).

## `safe_url`: print a URL without its password

```pycon
>>> from tabrion import safe_url
>>> safe_url("postgresql://app:s3cret@db.example.com:5432/shop")
'postgresql://app:***@db.example.com:5432/shop'
>>> safe_url("postgresql+psycopg://app:s3cret@db.example.com/shop?sslmode=require")
'postgresql+psycopg://app:***@db.example.com/shop?sslmode=require'
>>> safe_url("host=db.example.com user=app password=s3cret dbname=shop")
'host=db.example.com user=app password=*** dbname=shop'
>>> safe_url("postgresql://db.example.com/shop?user=app&password=s3cret")
'postgresql://db.example.com/shop?user=app&password=***'
```

It masks the password in URL form, in keyword conninfo and in a `password` query parameter. It never raises; input it
cannot parse safely is replaced as a whole:

```pycon
>>> safe_url("not a url ::")
'<unparseable url>'
```

`tabrion.safe_url` and `tabrion.config.safe_url` are the same function. `Database.safe_url` and `repr(Database)` use
it too ([Database and Profile](database.md)).

## `url_from_env`: read a URL or a secret file

`url_from_env(name)` reads the variable `name`, or the file named by `name + "_FILE"` (the Docker and Kubernetes
secret convention). For a file, the content without the trailing newline counts.

```pycon
>>> from pathlib import Path
>>> from tabrion.config import url_from_env
>>> url_from_env("DATABASE_URL", environ={"DATABASE_URL": "sqlite:///app.db"})
'sqlite:///app.db'
>>> _ = Path("db-url.txt").write_text("postgresql://app:s3cret@db/shop\n", encoding="utf-8")
>>> safe_url(url_from_env("DATABASE_URL", environ={"DATABASE_URL_FILE": "db-url.txt"}))
'postgresql://app:***@db/shop'
```

`environ` defaults to `os.environ`; pass a mapping in tests. The errors are `ConfigurationError`s whose messages name
the variable but never its value:

| Code | When |
|---|---|
| `config.missing` | neither `name` nor `name_FILE` is set (or both are empty) and `required=True` |
| `config.ambiguous` | both are set |
| `config.unreadable` | the file is missing, unreadable or not UTF-8 |
| `config.not_regular_file` | the file is a directory, FIFO or device |
| `config.too_large` | the file is larger than 64 KiB |

```pycon
>>> url_from_env("DATABASE_URL", environ={})
Traceback (most recent call last):
  ...
tabrion.errors.ConfigurationError: config.missing (name=DATABASE_URL)
>>> url_from_env("DATABASE_URL", environ={"DATABASE_URL": "a", "DATABASE_URL_FILE": "db-url.txt"})
Traceback (most recent call last):
  ...
tabrion.errors.ConfigurationError: config.ambiguous (name=DATABASE_URL)
>>> url_from_env("DATABASE_URL", environ={}, required=False) is None
True
```

`Database.from_env(name)` reads its URL through `url_from_env`, so the `_FILE` form works there as well.
