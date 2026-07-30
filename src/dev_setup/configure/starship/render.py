"""Turn a StarshipConfig into (a) a starship.toml and (b) an offline preview.

Both outputs are pure functions of the model tables, which is what keeps them from
drifting apart: neither one hard-codes a module list. The TOML is emitted as text
rather than serialised, because the header comments are part of the deliverable and
no TOML writer in the stdlib can produce them (SD-4).
"""

from __future__ import annotations

from typing import Any

from rich.markup import escape

from dev_setup.configure.starship.model import Section, StarshipConfig

# Powerline glyphs, as escapes with their Nerd Font names so they stay reviewable.
PL_CAP_LEFT = "\ue0b6"  # nf-pl-left_half_circle_thick
PL_ARROW = "\ue0b0"     # nf-pl-left_hard_divider
PL_ARROW_LEFT = "\ue0b2"  # nf-pl-right_hard_divider
PL_CAP_RIGHT = "\ue0b4"  # nf-pl-right_half_circle_thick

SCHEMA_URL = "https://starship.rs/config-schema.json"
DOCS_URL = "https://starship.rs/config/"


# ---------------------------------------------------------------------------
# TOML emission
# ---------------------------------------------------------------------------


def _lit(value: str) -> str:
    """A TOML *literal* string. Literal strings process no escapes, which is why
    starship format grammar (`$module`, `[text]($style)`, `\\[`) can be written
    verbatim. None of our values contain a single quote."""
    return f"'{value}'"


def _val(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    return _lit(str(value))


def _runs(sections: list[Section]) -> list[list[Section]]:
    """Group *consecutive* sections that share a palette role.

    Powerline draws one bar per run, so two adjacent language segments share a
    background instead of getting an arrow between two identical colours.
    """
    runs: list[list[Section]] = []
    for section in sections:
        if runs and runs[-1][0].role == section.role:
            runs[-1].append(section)
        else:
            runs.append([section])
    return runs


def _module_style(cfg: StarshipConfig, section: Section) -> str:
    if cfg.preset_spec.powerline:
        return f"fg:bar_text bg:{section.role}"
    return f"fg:{section.role}"


def _module_format(cfg: StarshipConfig, section: Section, *, right: bool) -> str:
    if cfg.preset_spec.powerline:
        return f"[ {section.body} ]($style)"
    # The separating space lives inside the module format so it vanishes along with
    # the module when it has nothing to show. On the right prompt it leads instead
    # of trails, so the last segment sits flush against the terminal edge.
    if right:
        return f"[ {section.body}]($style)"
    return f"[{section.body}]($style) "


def _left_format(cfg: StarshipConfig, left: list[Section]) -> list[str]:
    """The `format` value, as pieces to be joined with line continuations."""
    pieces: list[str] = []
    if cfg.preset_spec.powerline and left:
        runs = _runs(left)
        pieces.append(f"[{PL_CAP_LEFT}](fg:{runs[0][0].role})")
        for i, run in enumerate(runs):
            if i:
                prev = runs[i - 1][0].role
                pieces.append(f"[{PL_ARROW}](fg:{prev} bg:{run[0].role})")
            pieces.extend(f"${s.key}" for s in run)
        pieces.append(f"[{PL_ARROW}](fg:{runs[-1][0].role})")
    else:
        pieces.extend(f"${s.key}" for s in left)

    if cfg.layout_spec.two_line:
        pieces.append("$line_break")
    pieces.append("$character")
    return pieces


def _right_format(cfg: StarshipConfig, right: list[Section]) -> str:
    if not right:
        return ""
    if not cfg.preset_spec.powerline:
        return "".join(f"${s.key}" for s in right)

    runs = _runs(right)
    parts = [f"[{PL_ARROW_LEFT}](fg:{runs[0][0].role})"]
    for i, run in enumerate(runs):
        if i:
            prev = runs[i - 1][0].role
            parts.append(f"[{PL_ARROW_LEFT}](fg:{run[0].role} bg:{prev})")
        parts.extend(f"${s.key}" for s in run)
    parts.append(f"[{PL_CAP_RIGHT}](fg:{runs[-1][0].role})")
    return "".join(parts)


def _character_table(cfg: StarshipConfig) -> list[str]:
    symbol = cfg.preset_spec.prompt_symbol
    return [
        "[character]",
        f"success_symbol = {_lit(f'[{symbol}](bold fg:ok)')}",
        f"error_symbol = {_lit(f'[{symbol}](bold fg:err)')}",
    ]


def _section_table(cfg: StarshipConfig, section: Section, *, right: bool) -> list[str]:
    lines = [f"[{section.key}]"]
    lines.append(f"{section.style_key} = {_lit(_module_style(cfg, section))}")
    lines.append(f"format = {_lit(_module_format(cfg, section, right=right))}")
    if section.takes_symbol:
        # Emitted even when empty: without it starship falls back to its own glyph,
        # which would put a Nerd Font character into the `plain` preset.
        lines.append(f"symbol = {_lit(cfg.symbol(section))}")
    for key, value in section.extra.items():
        lines.append(f"{key} = {_val(value)}")
    return lines


def to_toml(cfg: StarshipConfig) -> str:
    """Render the full starship.toml. Top-level keys come first — in TOML every
    key after a `[table]` header belongs to that table."""
    left, right = cfg.split()
    preset, palette, layout = cfg.preset_spec, cfg.palette_spec, cfg.layout_spec

    out: list[str] = [
        "# Starship prompt configuration",
        "# Generated by `devstuff configure starship` — edit freely, or re-run the wizard.",
        f"# Style: {preset.label} · Palette: {palette.label} · Layout: {layout.label}",
        f"# Module reference: {DOCS_URL}",
        f'"$schema" = {_lit(SCHEMA_URL)}',
        "",
        f"add_newline = {_val(cfg.blank_line)}",
        f"palette = {_lit(palette.key)}",
        "",
        'format = """',
    ]
    out.append("\\\n".join(_left_format(cfg, left)) + '"""')

    right_fmt = _right_format(cfg, right)
    if right_fmt:
        out += ["", f"right_format = {_lit(right_fmt)}"]

    out += ["", "# Semantic colour roles — swap these to retheme every section at once.",
            f"[palettes.{palette.key}]"]
    out += [f"{role} = {_lit(palette.colors[role])}" for role in palette.colors]

    out += [""] + _character_table(cfg)
    for section in left:
        out += [""] + _section_table(cfg, section, right=False)
    for section in right:
        out += [""] + _section_table(cfg, section, right=True)

    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# Offline preview
# ---------------------------------------------------------------------------


def _rich_color(color: str) -> str:
    """Palette colour → Rich colour. Hex passes through; ANSI names differ only in
    the separator (`bright-black` vs Rich's `bright_black`)."""
    return color if color.startswith("#") else color.replace("-", "_")


def _sample_parts(cfg: StarshipConfig, sections: list[Section]) -> list[tuple[str, str]]:
    """(markup, plain text) for each rendered chunk, so the caller can measure width."""
    parts: list[tuple[str, str]] = []
    if not sections:
        return parts

    if cfg.preset_spec.powerline:
        text = _rich_color(cfg.color("bar_text"))
        runs = _runs(sections)
        first = _rich_color(cfg.color(runs[0][0].role))
        parts.append((f"[{first}]{PL_CAP_LEFT}[/]", PL_CAP_LEFT))
        for i, run in enumerate(runs):
            bg = _rich_color(cfg.color(run[0].role))
            if i:
                prev = _rich_color(cfg.color(runs[i - 1][0].role))
                parts.append((f"[{prev} on {bg}]{PL_ARROW}[/]", PL_ARROW))
            for section in run:
                body = f"{cfg.symbol(section)}{section.sample}"
                parts.append((f"[{text} on {bg}] {escape(body)} [/]", f" {body} "))
        last = _rich_color(cfg.color(runs[-1][0].role))
        parts.append((f"[{last}]{PL_ARROW}[/]", PL_ARROW))
        return parts

    for i, section in enumerate(sections):
        color = _rich_color(cfg.color(section.role))
        body = f"{cfg.symbol(section)}{section.sample}"
        prefix = "" if i == 0 else " "
        parts.append((f"{prefix}[{color}]{escape(body)}[/]", f"{prefix}{body}"))
    return parts


def sample_markup(cfg: StarshipConfig, width: int = 80) -> list[str]:
    """An approximation of the prompt as Rich markup lines.

    The fallback for when starship is not installed (FR-8) — it can be wrong about
    spacing, but never about which sections were selected, since it reads the same
    tables the emitter does.
    """
    left_sections, right_sections = cfg.split()
    left = _sample_parts(cfg, left_sections)
    right = _sample_parts(cfg, right_sections)

    symbol = cfg.preset_spec.prompt_symbol
    char = f"[bold {_rich_color(cfg.color('ok'))}]{escape(symbol)}[/]"

    line = "".join(m for m, _ in left)
    right_markup = "".join(m for m, _ in right)
    right_len = sum(len(p) for _, p in right)

    lines: list[str] = []
    if cfg.layout_spec.two_line:
        lines.append(line)
        # The right prompt goes on the cursor line, which is where a shell's RPROMPT
        # draws it — matching what the live preview shows.
        char_line = f"{char} "
        if right:
            char_line += " " * max(1, width - len(symbol) - 1 - right_len) + right_markup
        lines.append(char_line)
    else:
        head = f"{line} " if line else ""
        tail = f" {right_markup}" if right else ""
        lines.append(f"{head}{char} {tail}".rstrip() + " ")
    return lines
