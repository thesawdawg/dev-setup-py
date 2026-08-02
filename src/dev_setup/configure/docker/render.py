"""Turn a `DockerConfig` into `daemon.json`, and describe it for the review screen.

`daemon.json` is plain JSON, so unlike the pre-commit emitter there are no comments
to preserve and no quoting rules to get wrong — `json.dumps` is the whole emitter.
What this module is careful about instead is *what goes in*:

- Only settings that differ from the daemon's own defaults are written. A file
  restating the defaults freezes them, so a future Docker changing a default would
  never reach a machine carrying one.
- `log-opts` values are always strings. This is the one type error
  `dockerd --validate` does catch, and it catches it by refusing to start.
- Unrecognised keys read from an existing file are carried through untouched.

`matches()` still exists, and is not vacuous: it asserts `data()` emits JSON-native
types. The address pools are tuples in the model and must be lists in the file.
"""

from __future__ import annotations

import json

from dev_setup.configure.docker.model import (
    GROUPS,
    SETTINGS,
    DockerConfig,
)

# daemon.json has no comment syntax, so the file cannot carry a marker saying the
# wizard wrote it. The overwrite path shows a diff and asks instead — see SD-4.
KEY_ORDER = [
    "log-driver",
    "log-opts",
    "live-restore",
    "shutdown-timeout",
    "max-concurrent-downloads",
    "default-address-pools",
    "dns",
    "userland-proxy",
    "registry-mirrors",
    "insecure-registries",
    "data-root",
    "storage-driver",
    "no-new-privileges",
    "icc",
    "metrics-addr",
    "debug",
]


def data(cfg: DockerConfig) -> dict[str, object]:
    """The daemon.json content as a plain, JSON-native structure.

    Ordered: modelled keys in `KEY_ORDER`, then anything carried over from an
    existing file. A key equal to the daemon's default is omitted entirely.
    """
    out: dict[str, object] = {}

    driver = cfg.driver()
    if driver.key != SETTINGS["log_driver"].default:
        out["log-driver"] = driver.key
    opts = cfg.log_opts()
    if opts:
        # Every value a string — a number here is the one thing `dockerd --validate`
        # rejects, and it rejects it by refusing to start the daemon.
        out["log-opts"] = {k: str(v) for k, v in opts.items()}

    if cfg.live_restore != SETTINGS["live_restore"].default:
        out["live-restore"] = cfg.live_restore
    if cfg.shutdown_timeout != SETTINGS["shutdown_timeout"].default:
        out["shutdown-timeout"] = int(cfg.shutdown_timeout)
    if cfg.max_concurrent_downloads != SETTINGS["max_concurrent_downloads"].default:
        out["max-concurrent-downloads"] = int(cfg.max_concurrent_downloads)

    if cfg.address_pools:
        # Tuples in the model, lists in the file. `matches()` is what keeps this
        # conversion from being forgotten.
        out["default-address-pools"] = [
            {"base": base, "size": int(size)} for base, size in cfg.address_pools
        ]
    if cfg.dns:
        out["dns"] = list(cfg.dns)
    if cfg.userland_proxy != SETTINGS["userland_proxy"].default:
        out["userland-proxy"] = cfg.userland_proxy

    if cfg.registry_mirrors:
        out["registry-mirrors"] = list(cfg.registry_mirrors)
    if cfg.insecure_registries:
        out["insecure-registries"] = list(cfg.insecure_registries)

    if cfg.data_root != SETTINGS["data_root"].default:
        out["data-root"] = cfg.data_root
    if cfg.storage_driver:
        out["storage-driver"] = cfg.storage_driver

    if cfg.no_new_privileges != SETTINGS["no_new_privileges"].default:
        out["no-new-privileges"] = cfg.no_new_privileges
    if cfg.icc != SETTINGS["icc"].default:
        out["icc"] = cfg.icc

    if cfg.metrics_addr:
        out["metrics-addr"] = cfg.metrics_addr
    if cfg.debug != SETTINGS["debug"].default:
        out["debug"] = cfg.debug

    ordered = {key: out[key] for key in KEY_ORDER if key in out}
    # Keys this wizard does not model, preserved exactly as they were read.
    for key, value in cfg.extra.items():
        if key not in ordered:
            ordered[key] = value
    return ordered


def to_json(cfg: DockerConfig) -> str:
    """The file as it will be written. Trailing newline: it is a config file."""
    return json.dumps(data(cfg), indent=2) + "\n"


def matches(text: str, cfg: DockerConfig) -> bool:
    """Whether the emitted text parses back to exactly what `data()` describes.

    JSON round-trips by construction, so what this really asserts is that `data()`
    produced JSON-native types — a tuple or a Path would come back as something
    else, or not survive `json.dumps` at all.
    """
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return False
    return parsed == json.loads(json.dumps(data(cfg)))


# ---------------------------------------------------------------------------
# Review screen
# ---------------------------------------------------------------------------


def summary(cfg: DockerConfig) -> list[tuple[str, str]]:
    rows = [
        ("starting point", cfg.preset),
        ("log driver", f"{cfg.log_driver} — {cfg.driver().description}"),
        ("log budget", cfg.log_budget()),
        ("writes to", str(cfg.target)),
    ]
    if cfg.extra:
        rows.append(("carried over", ", ".join(sorted(cfg.extra))))
    return rows


def setting_rows(cfg: DockerConfig) -> list[tuple[str, str, str, str]]:
    """(group, daemon.json key, value, why it matters) for everything being written."""
    rendered = data(cfg)
    rows: list[tuple[str, str, str, str]] = []
    for key, setting in cfg.changed().items():  # noqa: B007 — key documents the pairing
        json_key = setting.json_key
        if json_key not in rendered:
            continue
        rows.append((
            GROUPS[setting.group].label,
            json_key,
            _render_value(rendered[json_key]),
            setting.why,
        ))
    for json_key, value in rendered.items():
        if json_key not in {s.json_key for s in SETTINGS.values()}:
            rows.append(("Carried over", json_key, _render_value(value), "not set by this wizard"))
    return rows


def _render_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dict):
        return ", ".join(f"{k}={v}" for k, v in value.items())
    if isinstance(value, list):
        return ", ".join(
            f"{item['base']} /{item['size']}" if isinstance(item, dict) and "base" in item
            else str(item)
            for item in value
        )
    return str(value)


def diff(old: str, new: str) -> list[str]:
    """A unified diff between the file on disk and what is about to replace it.

    Because daemon.json cannot carry a "generated by" marker, this is what the
    overwrite prompt shows instead.
    """
    import difflib

    return list(
        difflib.unified_diff(
            old.splitlines(),
            new.splitlines(),
            fromfile="current",
            tofile="new",
            lineterm="",
        )
    )


__all__ = ["KEY_ORDER", "data", "diff", "matches", "setting_rows", "summary", "to_json"]
