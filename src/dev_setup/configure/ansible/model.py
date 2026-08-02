"""The data behind the ansible.cfg wizard. Everything else reads these tables.

**Every field below — the INI section, the key, the type and the default — was read
from `ansible-config list` on ansible-core 2.20, not recalled.** That matters more
here than in any of the other configurators, because ansible has moved settings
between sections across versions and the internet is full of `ansible.cfg` snippets
that no longer do anything.

The headline case: **`pipelining` lives in `[defaults]`, and `[ssh_connection]` is not
a section this ansible knows.** Measured — `[ssh_connection] pipelining = True`
produces *nothing* in `ansible-config dump --only-changed`, while the same setting
under `[defaults]` shows up as `ANSIBLE_PIPELINING`. That stanza is probably the most
copy-pasted three lines in Ansible, and on this version it is inert.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

CONFIG_FILE = "ansible.cfg"

# The sections this ansible reads, from `ansible-config list`. `ssh_connection` is
# deliberately absent — see the module docstring.
SECTIONS = (
    "defaults",
    "privilege_escalation",
    "inventory",
    "connection",
    "colors",
    "galaxy",
    "diff",
    "tags",
    "selinux",
    "persistent_connection",
    "jinja2",
    "netconf_connection",
)

# Sections that no longer exist but appear in most older configs and tutorials. A
# file carrying one parses fine and silently does nothing.
RETIRED_SECTIONS = {
    "ssh_connection": (
        "ansible-core no longer reads [ssh_connection]. `pipelining` moved to "
        "[defaults]; `ssh_args` and `control_path` are now connection-plugin "
        "settings set per-inventory or via environment variables."
    ),
    "accelerate": "The accelerate connection was removed in Ansible 2.4.",
    "paramiko": "Renamed to [paramiko_connection] and then removed from core.",
}


@dataclass(frozen=True)
class Group:
    key: str
    label: str
    description: str


GROUPS: dict[str, Group] = {
    "connection": Group("connection", "Connecting", "How ansible reaches its hosts"),
    "speed": Group("speed", "Speed", "What makes a run fast or slow"),
    "output": Group("output", "Output", "What a run prints while it happens"),
    "layout": Group("layout", "Project layout", "Where inventory, roles and collections live"),
    "become": Group("become", "Privilege escalation", "How ansible becomes another user"),
    "vault": Group("vault", "Vault", "How encrypted values are unlocked"),
}


@dataclass(frozen=True)
class Setting:
    """One ansible.cfg setting.

    `section`, `key`, `kind` and `default` are all as reported by
    `ansible-config list`; `env_name` is the name that appears in
    `ansible-config dump`, which is how `validate.py` proves the setting was read.
    """

    key: str  # the field name on AnsibleConfig
    section: str
    ini_key: str
    env_name: str
    label: str
    description: str
    group: str
    kind: str  # bool | int | str | path | list
    default: object = None
    choices: tuple[str, ...] = ()
    why: str = ""
    # Which `ansible-config dump` this setting shows up in. Core settings appear in
    # the plain dump; a plugin option only appears under `dump -t <type>`. This is
    # not a detail — `ansible-config validate` knows only core options, so it
    # reports a *false* error for a plugin option that works perfectly well. See
    # `plugin_option`.
    dump_type: str = ""

    @property
    def plugin_option(self) -> bool:
        return bool(self.dump_type)


SETTINGS: dict[str, Setting] = {
    "host_key_checking": Setting(
        key="host_key_checking",
        section="defaults",
        ini_key="host_key_checking",
        env_name="HOST_KEY_CHECKING",
        label="Check host keys",
        description="Refuse to connect to a host whose SSH key is unknown",
        group="connection",
        kind="bool",
        default=True,
        why="Turning it off is what makes unattended runs work, and what makes them "
        "vulnerable to a man in the middle. Do it for throwaway hosts, not production.",
    ),
    "remote_user": Setting(
        key="remote_user",
        section="defaults",
        ini_key="remote_user",
        env_name="DEFAULT_REMOTE_USER",
        label="Remote user",
        description="Who to log in as when the inventory does not say",
        group="connection",
        kind="str",
        default="",
    ),
    "private_key_file": Setting(
        key="private_key_file",
        section="defaults",
        ini_key="private_key_file",
        env_name="DEFAULT_PRIVATE_KEY_FILE",
        label="Private key file",
        description="SSH key used when the inventory does not name one",
        group="connection",
        kind="path",
        default="",
    ),
    "timeout": Setting(
        key="timeout",
        section="defaults",
        ini_key="timeout",
        env_name="DEFAULT_TIMEOUT",
        label="Connection timeout (seconds)",
        description="How long to wait for a host to answer",
        group="connection",
        kind="int",
        default=10,
    ),
    "forks": Setting(
        key="forks",
        section="defaults",
        ini_key="forks",
        env_name="DEFAULT_FORKS",
        label="Parallel hosts",
        description="How many hosts a task runs against at once",
        group="speed",
        kind="int",
        default=5,
        why="The default of 5 is the single biggest reason large inventories feel slow.",
    ),
    "pipelining": Setting(
        key="pipelining",
        section="defaults",
        ini_key="pipelining",
        env_name="ANSIBLE_PIPELINING",
        label="Pipelining",
        description="Send module code over the open SSH session instead of copying files",
        group="speed",
        kind="bool",
        default=False,
        why="Roughly halves the SSH round trips per task. Needs `requiretty` off in "
        "sudoers on the target, which is the default on modern distributions. "
        "Note this is a [defaults] setting now, not [ssh_connection].",
    ),
    "gathering": Setting(
        key="gathering",
        section="defaults",
        ini_key="gathering",
        env_name="DEFAULT_GATHERING",
        label="Fact gathering",
        description="When to collect facts about a host",
        group="speed",
        kind="str",
        default="implicit",
        choices=("implicit", "explicit", "smart"),
        why="`smart` skips re-gathering for a host already seen in this run.",
    ),
    "fact_caching_timeout": Setting(
        key="fact_caching_timeout",
        section="defaults",
        ini_key="fact_caching_timeout",
        env_name="CACHE_PLUGIN_TIMEOUT",
        label="Fact cache lifetime (seconds)",
        description="How long cached facts stay valid",
        group="speed",
        kind="int",
        default=86400,
    ),
    "stdout_callback": Setting(
        key="stdout_callback",
        section="defaults",
        ini_key="stdout_callback",
        env_name="DEFAULT_STDOUT_CALLBACK",
        label="Output style",
        description="Which callback plugin formats the run's output",
        group="output",
        kind="str",
        default="default",
        why="ansible-core ships three. Anything else needs the collection providing "
        "it to be installed.",
    ),
    "callback_result_format": Setting(
        key="callback_result_format",
        section="defaults",
        ini_key="callback_result_format",
        env_name="result_format",
        label="Result format",
        description="Print task results as JSON or as YAML",
        group="output",
        kind="str",
        default="json",
        choices=("json", "yaml"),
        # It is an option of the `default` callback plugin, not a core setting.
        dump_type="callback",
        why="This is how you get readable multi-line output — the old answer, "
        "`stdout_callback = yaml`, names a callback that was removed. Note that "
        "`ansible-config validate` reports this key as unknown: it only knows core "
        "options, and this is a plugin one. The setting works; the error is wrong.",
    ),
    "display_skipped_hosts": Setting(
        key="display_skipped_hosts",
        section="defaults",
        ini_key="display_skipped_hosts",
        env_name="DISPLAY_SKIPPED_HOSTS",
        label="Show skipped hosts",
        description="Print a line for every task that was skipped",
        group="output",
        kind="bool",
        default=True,
        why="Turning this off is the cheapest way to make a long run readable.",
    ),
    "nocows": Setting(
        key="nocows",
        section="defaults",
        ini_key="nocows",
        env_name="ANSIBLE_NOCOWS",
        label="Disable cowsay",
        description="Stop ansible drawing a cow when cowsay is installed",
        group="output",
        kind="bool",
        default=False,
    ),
    "log_path": Setting(
        key="log_path",
        section="defaults",
        ini_key="log_path",
        env_name="DEFAULT_LOG_PATH",
        label="Log file",
        description="Append every run's output to this file",
        group="output",
        kind="path",
        default="",
        why="Ansible does not rotate it, and it can contain secrets from failed tasks.",
    ),
    "inventory": Setting(
        key="inventory",
        section="defaults",
        ini_key="inventory",
        env_name="DEFAULT_HOST_LIST",
        label="Inventory",
        description="Default inventory file or directory",
        group="layout",
        kind="path",
        default="/etc/ansible/hosts",
    ),
    "roles_path": Setting(
        key="roles_path",
        section="defaults",
        ini_key="roles_path",
        env_name="DEFAULT_ROLES_PATH",
        label="Roles path",
        description="Where to look for roles",
        group="layout",
        kind="path",
        default="",
    ),
    "collections_path": Setting(
        key="collections_path",
        section="defaults",
        ini_key="collections_path",
        env_name="COLLECTIONS_PATHS",
        label="Collections path",
        description="Where to look for collections",
        group="layout",
        kind="path",
        default="",
    ),
    "interpreter_python": Setting(
        key="interpreter_python",
        section="defaults",
        ini_key="interpreter_python",
        env_name="INTERPRETER_PYTHON",
        label="Python interpreter",
        description="Which python to use on the target",
        group="layout",
        kind="str",
        default="auto",
        why="`auto_silent` is `auto` without the discovery warning on every run.",
    ),
    "retry_files_enabled": Setting(
        key="retry_files_enabled",
        section="defaults",
        ini_key="retry_files_enabled",
        env_name="RETRY_FILES_ENABLED",
        label="Write .retry files",
        description="Drop a .retry file listing failed hosts",
        group="layout",
        kind="bool",
        default=False,
    ),
    "become": Setting(
        key="become",
        section="privilege_escalation",
        ini_key="become",
        env_name="DEFAULT_BECOME",
        label="Become by default",
        description="Escalate privileges for every task unless told otherwise",
        group="become",
        kind="bool",
        default=False,
        why="A blanket become means every task runs as root, including ones that "
        "should not.",
    ),
    "become_method": Setting(
        key="become_method",
        section="privilege_escalation",
        ini_key="become_method",
        env_name="DEFAULT_BECOME_METHOD",
        label="Become method",
        description="How to escalate",
        group="become",
        kind="str",
        default="sudo",
        choices=("sudo", "su", "doas", "pbrun", "pfexec", "runas", "machinectl"),
    ),
    "become_user": Setting(
        key="become_user",
        section="privilege_escalation",
        ini_key="become_user",
        env_name="DEFAULT_BECOME_USER",
        label="Become user",
        description="Who to become",
        group="become",
        kind="str",
        default="root",
    ),
    "become_ask_pass": Setting(
        key="become_ask_pass",
        section="privilege_escalation",
        ini_key="become_ask_pass",
        env_name="DEFAULT_BECOME_ASK_PASS",
        label="Ask for the become password",
        description="Prompt for a password when escalating",
        group="become",
        kind="bool",
        default=False,
    ),
    "vault_password_file": Setting(
        key="vault_password_file",
        section="defaults",
        ini_key="vault_password_file",
        env_name="DEFAULT_VAULT_PASSWORD_FILE",
        label="Vault password file",
        description="File holding the vault password, or a script that prints it",
        group="vault",
        kind="path",
        default="",
        why="An executable file is run and its stdout used, which is how a vault "
        "password comes out of a keychain rather than off the disk.",
    ),
}

# The stdout callbacks ansible-core actually ships, read from
# `ansible-doc -t callback -l`. There are exactly three.
#
# `yaml` and `debug` are deliberately absent, and their absence is the correction
# that most changed this file. `stdout_callback = yaml` is the standard advice
# everywhere, and the plugin was **removed**: ansible-core 2.20 reports "The
# 'community.general.yaml' callback plugin has been removed ... superseded by the
# option `result_format=yaml` in callback plugin ansible.builtin.default from
# ansible-core 2.13 onwards". Setting it is accepted by every check and produces
# JSON output anyway. The replacement is the `callback_result_format` setting above,
# whose INI spelling is `[defaults] callback_result_format` — verified by running a
# real task and watching the output become YAML.
STDOUT_CALLBACKS: dict[str, str] = {
    "default": "Ansible's standard output",
    "minimal": "One line per task result, nothing else",
    "oneline": "Everything on a single line per result — for piping into grep",
}

# `ansible-doc -t callback -l` prints fully-qualified names (`ansible.builtin.default`),
# so a short name has to be resolved before it can be looked up — otherwise every
# builtin looks unavailable.
BUILTIN_PREFIX = "ansible.builtin."


@dataclass(frozen=True)
class Preset:
    key: str
    label: str
    description: str
    values: dict[str, object] = field(default_factory=dict)


PRESETS: dict[str, Preset] = {
    "project": Preset(
        key="project",
        label="Project defaults",
        description="Readable output, sensible paths, and the speed settings that matter.",
        values={
            "inventory": "./inventory",
            "roles_path": "./roles",
            "collections_path": "./collections",
            "display_skipped_hosts": False,
            "nocows": True,
            "forks": 20,
            "pipelining": True,
            "gathering": "smart",
            "interpreter_python": "auto_silent",
        },
    ),
    "fast": Preset(
        key="fast",
        label="Fast",
        description="Tuned for large inventories: many forks, pipelining, cached facts.",
        values={
            "forks": 50,
            "pipelining": True,
            "gathering": "smart",
            "fact_caching_timeout": 7200,
            "display_skipped_hosts": False,
            "nocows": True,
        },
    ),
    "ci": Preset(
        key="ci",
        label="CI runner",
        description="Unattended: no host-key prompts, no cows, terse output, a log file.",
        values={
            "host_key_checking": False,
            "nocows": True,
            "display_skipped_hosts": False,
            "stdout_callback": "minimal",
            "forks": 20,
            "pipelining": True,
            "retry_files_enabled": False,
        },
    ),
    "vault": Preset(
        key="vault",
        label="With vault",
        description="Project defaults plus a vault password file, so `--ask-vault-pass` goes away.",
        values={
            "inventory": "./inventory",
            "roles_path": "./roles",
            "nocows": True,
            "forks": 20,
            "pipelining": True,
            "vault_password_file": "./.vault-pass",
        },
    ),
    "become": Preset(
        key="become",
        label="Escalating by default",
        description="Project defaults, plus become for every task.",
        values={
            "inventory": "./inventory",
            "roles_path": "./roles",
            "nocows": True,
            "forks": 20,
            "pipelining": True,
            "become": True,
            "become_method": "sudo",
            "become_user": "root",
        },
    ),
    "current": Preset(
        key="current",
        label="Whatever is configured now",
        description="Start from the existing ansible.cfg and adjust it.",
        values={},
    ),
    "empty": Preset(
        key="empty",
        label="Start from nothing",
        description="An empty config — every ansible default, explicitly.",
        values={},
    ),
}

DEFAULT_PRESET = "project"


@dataclass
class AnsibleConfig:
    preset: str = DEFAULT_PRESET

    host_key_checking: bool = True
    remote_user: str = ""
    private_key_file: str = ""
    timeout: int = 10

    forks: int = 5
    pipelining: bool = False
    gathering: str = "implicit"
    fact_caching_timeout: int = 86400

    stdout_callback: str = "default"
    callback_result_format: str = "json"
    display_skipped_hosts: bool = True
    nocows: bool = False
    log_path: str = ""

    inventory: str = "/etc/ansible/hosts"
    roles_path: str = ""
    collections_path: str = ""
    interpreter_python: str = "auto"
    retry_files_enabled: bool = False

    become: bool = False
    become_method: str = "sudo"
    become_user: str = "root"
    become_ask_pass: bool = False

    vault_password_file: str = ""

    # Sections and keys read from an existing file that this wizard does not model,
    # kept as {section: {key: value}} and written back untouched.
    extra: dict[str, dict[str, str]] = field(default_factory=dict)

    target: Path = Path(CONFIG_FILE)

    # -- derived views ------------------------------------------------------

    def changed(self) -> dict[str, Setting]:
        """Settings differing from ansible's own defaults, in table order."""
        out: dict[str, Setting] = {}
        for key, setting in SETTINGS.items():
            value = getattr(self, key)
            if setting.kind in ("str", "path") and not value and not setting.default:
                continue
            if value != setting.default:
                out[key] = setting
        return out

    def by_section(self) -> dict[str, list[Setting]]:
        """The settings being written, grouped into the INI sections they belong to.

        The grouping comes from `Setting.section`, which came from
        `ansible-config list` — this is what stops a setting being written into a
        section this ansible does not read.
        """
        out: dict[str, list[Setting]] = {}
        for setting in self.changed().values():
            out.setdefault(setting.section, []).append(setting)
        for section in self.extra:
            out.setdefault(section, [])
        return {name: out[name] for name in SECTIONS if name in out} | {
            name: out[name] for name in out if name not in SECTIONS
        }

    def expected_env(self, dump_type: str = "") -> dict[str, Setting]:
        """{name as it appears in `ansible-config dump`: setting} for what is written.

        This is the contract `validate` checks: every one of these must show up in
        the corresponding `ansible-config dump --only-changed`, sourced from our
        file. `dump_type` selects which dump — core settings appear in the plain
        one, a plugin option only under `dump -t <type>`.
        """
        return {
            setting.env_name: setting
            for setting in self.changed().values()
            if setting.dump_type == dump_type
        }

    def dump_types(self) -> list[str]:
        """The `-t` values needed to see everything this config sets."""
        return sorted({setting.dump_type for setting in self.changed().values()})

    def plugin_options(self) -> list[Setting]:
        return [s for s in self.changed().values() if s.plugin_option]

    def warnings(self) -> list[str]:
        out: list[str] = []
        if not self.host_key_checking:
            out.append(
                "Host key checking is off: ansible will connect to a host presenting "
                "any key at all, so a man in the middle is undetectable."
            )
        if self.become and not self.become_ask_pass:
            out.append(
                "Every task escalates by default and no password will be asked for — "
                "this needs passwordless sudo on every target."
            )
        if self.vault_password_file:
            out.append(
                f"Keep {self.vault_password_file} out of git, and readable only by you "
                "(chmod 600). An executable file is run instead of read, which is how "
                "the password can come from a keychain."
            )
        if self.log_path:
            out.append(
                f"{self.log_path} is appended to forever — ansible does not rotate it — "
                "and failed tasks can put secrets in it."
            )
        if self.forks > 100:
            out.append(
                f"{self.forks} forks means {self.forks} SSH connections at once; the "
                "control machine's file descriptor limit is usually what breaks first."
            )
        for setting in self.plugin_options():
            out.append(
                f"{setting.ini_key} is a {setting.dump_type}-plugin option, so "
                "`ansible-config validate` reports it as an unknown key. It works "
                "anyway — the validator only knows core settings."
            )
        if self.pipelining and self.become and self.become_method == "su":
            out.append("Pipelining does not work with `su` as the become method.")
        return out


__all__ = [
    "CONFIG_FILE",
    "DEFAULT_PRESET",
    "GROUPS",
    "PRESETS",
    "RETIRED_SECTIONS",
    "SECTIONS",
    "SETTINGS",
    "BUILTIN_PREFIX",
    "STDOUT_CALLBACKS",
    "AnsibleConfig",
    "Group",
    "Preset",
    "Setting",
]
