"""Profile file format: strict loading and deterministic dumping (docs/specs/profile, P1).

The loader's job is mostly to *refuse*. A profile says what should be installed, so a value
that YAML quietly changed on the way in — `1.10` becoming `1.1` — is worse than an error.
"""

from __future__ import annotations

import getpass
import re
import socket
from pathlib import Path

import pytest

from dev_setup.profile import Entry, Profile, ProfileError, dumps, load, loads


def ok(text: str) -> Profile:
    return loads(text, source="p.yaml")


def err(text: str) -> str:
    with pytest.raises(ProfileError) as exc:
        loads(text, source="p.yaml")
    return str(exc.value)


# -- accepted spellings (FR-1, FR-2, FR-6) ------------------------------------------------


def test_minimal_profile():
    assert ok("version: 1\ntools: {}\n") == Profile()


def test_both_spellings_of_an_empty_entry_mean_unpinned():
    p = ok("version: 1\ntools:\n  uv: {}\n  gh:\n")

    assert p.tools == {"uv": Entry(), "gh": Entry()}
    assert p.tools["uv"].version is None


def test_a_pinned_entry():
    p = ok("version: 1\ntools:\n  lazygit:\n    version: 0.45.0\n")

    assert p.tools["lazygit"] == Entry(version="0.45.0")


@pytest.mark.parametrize(
    "value",
    ["0.45.0", "v1.2", "1e3", "'1.10'", '"0.40"', "'2'", "1.0.0-rc.1", "1:2.3-4"],
)
def test_versions_that_load_as_strings_are_kept_verbatim(value):
    p = ok(f"version: 1\ntools:\n  x:\n    version: {value}\n")

    assert p.tools["x"].version == value.strip("'\"")


def test_a_key_the_catalog_does_not_know_still_loads():
    # FR-6: a profile from a machine with custom tools must load on one without them.
    p = ok("version: 1\ntools:\n  definitely-not-a-real-tool: {}\n")

    assert "definitely-not-a-real-tool" in p.tools


def test_file_order_is_preserved_by_the_loader():
    p = ok("version: 1\ntools:\n  zeta: {}\n  alpha: {}\n")

    assert list(p.tools) == ["zeta", "alpha"]


# -- the file envelope (FR-3) -------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("", "empty"),
        ("# only a comment\n", "empty"),
        ("- a\n- b\n", "mapping"),
        ("just a string\n", "mapping"),
        ("tools: {}\n", "version"),
        ("version: 2\ntools: {}\n", "version"),
        ("version: '1'\ntools: {}\n", "version"),
        ("version: true\ntools: {}\n", "version"),  # True == 1 in Python; must not pass
        ("version: 1.0\ntools: {}\n", "version"),
        ("version: 1\n", "tools"),
        ("version: 1\ntools:\n", "tools"),
        ("version: 1\ntools: []\n", "tools"),
        ("version: 1\ntools: uv\n", "tools"),
        ("version: 1\ntools: {}\nextra: 1\n", "extra"),
    ],
)
def test_bad_envelope_is_rejected_with_the_file_named(text, fragment):
    message = err(text)

    assert "p.yaml" in message
    assert fragment in message


def test_invalid_yaml_syntax_is_a_profile_error_not_a_yaml_traceback():
    message = err("version: 1\ntools: {unclosed\n")

    assert "p.yaml" in message


# -- entries (FR-3) -----------------------------------------------------------------------


@pytest.mark.parametrize("entry", ["latest", "[1, 2]", "3", "true"])
def test_an_entry_that_is_not_a_mapping_is_rejected(entry):
    message = err(f"version: 1\ntools:\n  uv: {entry}\n")

    assert "uv" in message and "mapping" in message


def test_an_unknown_entry_field_is_rejected_and_named():
    message = err("version: 1\ntools:\n  uv:\n    pin: 1.0\n")

    assert "uv" in message and "pin" in message


@pytest.mark.parametrize("key", ["123", "true", "on", "yes", "null"])
def test_a_tool_key_that_yaml_reads_as_a_non_string_is_rejected(key):
    # PyYAML reads `on`/`yes`/`true` as True, `null` as None and `123` as an int — a tool key
    # must not silently become one of those. (`y` and `n` stay strings in PyYAML.)
    message = err(f"version: 1\ntools:\n  {key}: {{}}\n")

    assert "string" in message


# -- versions YAML would corrupt (FR-4; spec F-5) --------------------------------------------


@pytest.mark.parametrize(
    "unquoted",
    ["1.10", "0.40", "0.45", "2", "2026-09-30", "true", "1.0", "0x1F", "1_000", "1:30", "~"],
)
def test_an_unquoted_version_that_yaml_does_not_read_as_a_string_is_rejected(unquoted):
    message = err(f"version: 1\ntools:\n  lazygit:\n    version: {unquoted}\n")

    assert "p.yaml" in message
    assert "lazygit" in message
    assert "quote" in message.lower()


def test_an_empty_version_value_is_rejected():
    message = err("version: 1\ntools:\n  lazygit:\n    version:\n")

    assert "lazygit" in message and "version" in message


@pytest.mark.parametrize("value", ["''", "' 1.0'", "'1.0 '", "'  '"])
def test_a_blank_or_padded_version_string_is_rejected(value):
    message = err(f"version: 1\ntools:\n  lazygit:\n    version: {value}\n")

    assert "lazygit" in message


# -- duplicate keys (FR-5; spec F-5) ----------------------------------------------------------


def test_a_duplicate_tool_key_is_an_error_naming_the_key_and_line():
    message = err("version: 1\ntools:\n  uv: {}\n  gh: {}\n  uv:\n    version: '0.1'\n")

    assert "uv" in message
    assert "duplicate" in message.lower()
    assert re.search(r"line \d+", message)


def test_a_duplicate_top_level_field_is_an_error():
    message = err("version: 1\ntools:\n  a: {}\ntools:\n  b: {}\n")

    assert "tools" in message and "duplicate" in message.lower()


def test_a_duplicate_field_inside_an_entry_is_an_error():
    message = err("version: 1\ntools:\n  uv:\n    version: '1'\n    version: '2'\n")

    assert "duplicate" in message.lower()


# -- load() from disk -----------------------------------------------------------------------


def test_load_reads_a_file(tmp_path: Path):
    f = tmp_path / "work.yaml"
    f.write_text("version: 1\ntools:\n  uv: {}\n")

    assert load(f).tools == {"uv": Entry()}


def test_load_names_the_path_in_errors(tmp_path: Path):
    f = tmp_path / "bad.yaml"
    f.write_text("version: 2\ntools: {}\n")

    with pytest.raises(ProfileError, match="bad.yaml"):
        load(f)


def test_load_of_a_missing_file_is_a_profile_error(tmp_path: Path):
    with pytest.raises(ProfileError, match="nope.yaml"):
        load(tmp_path / "nope.yaml")


def test_load_of_a_directory_is_a_profile_error(tmp_path: Path):
    with pytest.raises(ProfileError):
        load(tmp_path)


def test_load_of_undecodable_bytes_is_a_profile_error(tmp_path: Path):
    f = tmp_path / "binary.yaml"
    f.write_bytes(b"\xff\xfe\x00not utf-8 \x80\x81")

    with pytest.raises(ProfileError, match="binary.yaml"):
        load(f)


def test_profile_error_is_a_runtime_error_like_catalog_error():
    assert issubclass(ProfileError, RuntimeError)


# -- dumping (FR-9, NFR-4) -------------------------------------------------------------------


def test_dump_is_sorted_by_key_whatever_the_input_order():
    a = Profile({"zeta": Entry(), "alpha": Entry(), "mid": Entry("1.0")})
    b = Profile({"alpha": Entry(), "mid": Entry("1.0"), "zeta": Entry()})

    assert dumps(a) == dumps(b)
    assert list(loads(dumps(a)).tools) == ["alpha", "mid", "zeta"]


def test_dump_is_deterministic():
    p = Profile({"uv": Entry(), "lazygit": Entry("0.45.0")})

    assert dumps(p) == dumps(p)


def test_dump_leaks_nothing_about_the_machine():
    out = dumps(Profile({"uv": Entry(), "lazygit": Entry("0.45.0")}))

    for private in (socket.gethostname(), getpass.getuser(), str(Path.home())):
        if private:  # a container may report an empty hostname
            assert private not in out
    assert not re.search(r"\d{4}-\d{2}-\d{2}", out), "a date/timestamp crept into the output"
    assert not re.search(r"\d{2}:\d{2}", out)


def test_dump_has_a_static_comment_header_and_is_valid_yaml():
    out = dumps(Profile({"uv": Entry()}))

    assert out.startswith("# ")
    assert "version: 1" in out
    assert loads(out) == Profile({"uv": Entry()})


def test_dump_of_an_empty_profile_loads_back():
    out = dumps(Profile())

    assert "tools: {}" in out
    assert loads(out) == Profile()


def test_unpinned_entries_dump_as_empty_mappings():
    assert "uv: {}" in dumps(Profile({"uv": Entry()}))


# The inputs below are exactly where a hand-rolled emitter would corrupt data and where
# `yaml.safe_dump` must be shown to hold: strings that reload as numbers, dates, booleans, null.
TRICKY = [
    "0.45", "1.10", "2", "0.40", "1e3", "2026-09-30", "true", "False", "null", "~", "0x1F",
    "0o17", "1_000", "1:30", "yes", "No", "on", "OFF", "1.0.0-rc.1", "v1.2", "1:2.3-4ubuntu1",
    "00", "+1", "-1", ".5", "1,000",
]


@pytest.mark.parametrize("version", TRICKY)
def test_round_trip_of_versions_that_look_like_other_types(version):
    p = Profile({"tool": Entry(version)})

    back = loads(dumps(p))

    assert back == p
    assert back.tools["tool"].version == version
    assert type(back.tools["tool"].version) is str


@pytest.mark.parametrize("key", ["on", "off", "yes", "no", "y", "n", "null", "true", "123", "1.5", "~"])
def test_round_trip_of_keys_yaml_would_read_as_non_strings(key):
    p = Profile({key: Entry()})

    assert loads(dumps(p)) == p
    assert list(loads(dumps(p)).tools) == [key]


def test_round_trip_of_a_realistic_profile():
    p = Profile(
        {
            "uv": Entry(),
            "claude-code": Entry("2.1.296"),
            "ipython": Entry("9.17.1"),
            "hey-dave": Entry("0.2.0a1"),
            "reptyr": Entry(),
        }
    )

    assert loads(dumps(p)) == p


# -- the model -------------------------------------------------------------------------------


def test_entry_and_profile_are_value_objects():
    assert Entry("1.0") == Entry("1.0")
    assert Entry() != Entry("1.0")
    assert Profile({"a": Entry()}) == Profile({"a": Entry()})
    with pytest.raises(AttributeError):
        Entry().version = "2"  # type: ignore[misc]


def test_default_profile_is_empty_and_instances_do_not_share_state():
    a, b = Profile(), Profile()

    assert a.tools == {} and a.tools is not b.tools


# -- an Entry built in code obeys the same rules as one loaded from a file ---------------------
# Otherwise `snapshot` could construct a version that `load` then refuses to read back.


@pytest.mark.parametrize("bad", ["", "   ", " 1.0", "1.0 ", "\t1", 1.1, 2, True, ["1"]])
def test_entry_rejects_a_version_that_load_would_reject(bad):
    with pytest.raises(ProfileError):
        Entry(bad)  # type: ignore[arg-type]


def test_entry_accepts_none_and_ordinary_strings():
    assert Entry(None).version is None
    assert Entry("1.10").version == "1.10"
