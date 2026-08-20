"""A curated catalog of Nerd Font glyphs and emoji for the icon picker.

Every entry has a stable ``key``, a human-readable ``label`` (what the filterable
picker searches against), the ``glyph`` string that gets emitted into the config,
and one or more ``categories`` so the picker can group them. Glyphs are written as
escapes with their Nerd Font names so the source stays reviewable without the font
installed — the same convention as ``model.py``'s section icons.

The catalog is deliberately not exhaustive: it covers the domains the wizard's
sections live in (languages, cloud, git, shell, general) with a handful of
alternatives each, plus a set of generic shapes that work for anything. A user
who wants a glyph not listed here can type one into the picker's free-text input.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Icon:
    key: str
    label: str
    glyph: str
    categories: tuple[str, ...] = ()


# Category constants — keep in sync with the wizard's grouping.
CAT_LANG = "Languages"
CAT_CLOUD = "Cloud & Infrastructure"
CAT_GIT = "Git"
CAT_SHELL = "Shell"
CAT_GENERAL = "General"


ICONS: tuple[Icon, ...] = (
    # -- General -----------------------------------------------------------------
    Icon("folder", "Folder", "\uf07b ", (CAT_GENERAL,)),          # nf-fa-folder
    Icon("folder_open", "Open folder", "\uf07c ", (CAT_GENERAL,)),  # nf-fa-folder_open
    Icon("package", "Package", "\U000f03d7 ", (CAT_GENERAL,)),    # nf-md-package_variant_closed
    Icon("package_box", "Package box", "\uf1b6 ", (CAT_GENERAL,)),  # nf-fa-inbox
    Icon("container", "Container", "\U000f021b ", (CAT_GENERAL,)),  # nf-md-container
    Icon("cube", "Cube", "\uf1b2 ", (CAT_GENERAL,)),              # nf-fa-cube
    Icon("gear", "Gear", "\uf013 ", (CAT_GENERAL,)),              # nf-fa-cog
    Icon("wrench", "Wrench", "\uf0ad ", (CAT_GENERAL,)),          # nf-fa-wrench
    Icon("rocket", "Rocket", "\uf135 ", (CAT_GENERAL,)),          # nf-fa-rocket
    Icon("star", "Star", "\uf005 ", (CAT_GENERAL,)),              # nf-fa-star
    Icon("heart", "Heart", "\uf004 ", (CAT_GENERAL,)),            # nf-fa-heart
    Icon("check", "Check mark", "\uf00c ", (CAT_GENERAL,)),       # nf-fa-check
    Icon("x", "X mark", "\uf00d ", (CAT_GENERAL,)),               # nf-fa-times
    Icon("lightbulb", "Light bulb", "\uf0eb ", (CAT_GENERAL,)),   # nf-fa-lightbulb_o
    Icon("fire", "Fire", "\uf06d ", (CAT_GENERAL,)),              # nf-fa-fire
    Icon("bolt", "Lightning bolt", "\uf0e7 ", (CAT_GENERAL,)),    # nf-fa-bolt
    Icon("terminal", "Terminal", "\uf120 ", (CAT_GENERAL,)),      # nf-fa-terminal
    Icon("keyboard", "Keyboard", "\uf11c ", (CAT_GENERAL,)),      # nf-fa-keyboard_o
    Icon("clock", "Clock", "\uf017 ", (CAT_GENERAL,)),            # nf-fa-clock_o
    Icon("hourglass", "Hourglass", "\uf252 ", (CAT_GENERAL,)),    # nf-fa-hourglass_half
    Icon("chart", "Bar chart", "\uf080 ", (CAT_GENERAL,)),        # nf-fa-bar_chart
    Icon("database", "Database", "\uf1c0 ", (CAT_GENERAL,)),      # nf-fa-database
    Icon("server", "Server", "\uf233 ", (CAT_GENERAL,)),          # nf-fa-server
    Icon("globe", "Globe", "\uf0ac ", (CAT_GENERAL,)),            # nf-fa-globe
    Icon("link", "Link", "\uf0c1 ", (CAT_GENERAL,)),              # nf-fa-link

    # -- Git ---------------------------------------------------------------------
    Icon("git_branch", "Git branch", "\ue0a0 ", (CAT_GIT,)),       # nf-pl-branch
    Icon("git_branch_alt", "Git branch (alt)", "\uf126 ", (CAT_GIT,)),  # nf-fa-code_fork
    Icon("git_commit", "Git commit", "\ue0a1 ", (CAT_GIT,)),       # nf-pl-narrow
    Icon("git_merge", "Git merge", "\ue0a3 ", (CAT_GIT,)),         # nf-pl-branch
    Icon("git_pull", "Git pull request", "\uf09b ", (CAT_GIT,)),   # nf-fa-github_alt
    Icon("fork", "Fork", "\uf126 ", (CAT_GIT,)),                  # nf-fa-code_fork
    Icon("diff", "Diff", "\ue728 ", (CAT_GIT,)),                   # nf-seti-git

    # -- Languages ---------------------------------------------------------------
    Icon("nodejs_small", "Node.js small", "\ue718 ", (CAT_LANG,)),  # nf-dev-nodejs_small
    Icon("nodejs", "Node.js", "\ue74e ", (CAT_LANG,)),             # nf-dev-nodejs_large
    Icon("js", "JavaScript", "\ue74e ", (CAT_LANG,)),
    Icon("js_square", "JavaScript (square)", "\ue781 ", (CAT_LANG,)),  # nf-dev-javascript
    Icon("typescript", "TypeScript", "\ue628 ", (CAT_LANG,)),      # nf-seti-file_type_ts
    Icon("deno", "Deno", "\ue70c ", (CAT_LANG,)),                  # nf-seti-file_type_deno
    Icon("deno_robot", "Deno (robot)", "\U000f06a9 ", (CAT_LANG,)),  # nf-md-robot
    Icon("bun", "Bun (croissant)", "\U000f07f0 ", (CAT_LANG,)),    # nf-md-food_croissant
    Icon("python", "Python", "\ue73c ", (CAT_LANG,)),              # nf-dev-python
    Icon("python_alt", "Python (alt)", "\ue235 ", (CAT_LANG,)),    # nf-fa-python
    Icon("rust", "Rust", "\ue7a8 ", (CAT_LANG,)),                  # nf-dev-rust
    Icon("rust_crab", "Rust (crab)", "\U000f0f80 ", (CAT_LANG,)),  # nf-md-crab
    Icon("go", "Go", "\ue627 ", (CAT_LANG,)),                      # nf-seti-go
    Icon("go_gopher", "Go (gopher)", "\ue626 ", (CAT_LANG,)),      # nf-seti-go
    Icon("java", "Java", "\ue256 ", (CAT_LANG,)),                  # nf-dev-java
    Icon("java_cup", "Java (cup)", "\uf266 ", (CAT_LANG,)),        # nf-fa-java
    Icon("php", "PHP", "\ue73d ", (CAT_LANG,)),                    # nf-dev-php
    Icon("php_elephant", "PHP (elephant)", "\ue608 ", (CAT_LANG,)),  # nf-seti-php
    Icon("ruby", "Ruby", "\ue791 ", (CAT_LANG,)),                  # nf-dev-ruby
    Icon("ruby_gem", "Ruby (gem)", "\uf219 ", (CAT_LANG,)),        # nf-fa-ruby
    Icon("elixir", "Elixir (droplet)", "\U000f078b ", (CAT_LANG,)),  # nf-md-droplet
    Icon("elixir_alt", "Elixir", "\ue62d ", (CAT_LANG,)),          # nf-seti-elixir
    Icon("dotnet", ".NET", "\U000f071e ", (CAT_LANG,)),            # nf-md-microsoft_dot_net
    Icon("csharp", "C#", "\U000f031b ", (CAT_LANG,)),              # nf-md-language_csharp
    Icon("cpp", "C++", "\U000f061b ", (CAT_LANG,)),               # nf-md-language_cpp
    Icon("c", "C", "\U000f061c ", (CAT_LANG,)),                   # nf-md-language_c
    Icon("swift", "Swift", "\ue755 ", (CAT_LANG,)),                # nf-dev-swift
    Icon("kotlin", "Kotlin", "\ue634 ", (CAT_LANG,)),              # nf-seti-file_type_kotlin
    Icon("scala", "Scala", "\ue737 ", (CAT_LANG,)),                # nf-dev-scala
    Icon("haskell", "Haskell", "\ue77f ", (CAT_LANG,)),            # nf-dev-haskell
    Icon("lua", "Lua", "\ue620 ", (CAT_LANG,)),                    # nf-seti-file_type_lua
    Icon("perl", "Perl", "\ue769 ", (CAT_LANG,)),                  # nf-dev-perl
    Icon("clojure", "Clojure", "\ue76a ", (CAT_LANG,)),            # nf-dev-clojure
    Icon("zig", "Zig", "\U000f071f ", (CAT_LANG,)),               # nf-md-language_zig

    # -- Cloud & Infrastructure --------------------------------------------------
    Icon("docker", "Docker", "\U000f0868 ", (CAT_CLOUD,)),         # nf-md-docker
    Icon("docker_whale", "Docker (whale)", "\uf308 ", (CAT_CLOUD,)),  # nf-fa-docker
    Icon("kubernetes", "Kubernetes", "\U000f10fe ", (CAT_CLOUD,)),  # nf-md-kubernetes
    Icon("kubernetes_alt", "Kubernetes (alt)", "\ue0b6 ", (CAT_CLOUD,)),
    Icon("aws", "AWS", "\ue7ad ", (CAT_CLOUD,)),                   # nf-dev-aws
    Icon("aws_alt", "AWS (alt)", "\uf270 ", (CAT_CLOUD,)),         # nf-fa-aws
    Icon("gcp", "Google Cloud", "\ue7b3 ", (CAT_CLOUD,)),          # nf-dev-google_cloud_platform
    Icon("gcp_cloud", "Google Cloud (cloud)", "\uf0c2 ", (CAT_CLOUD,)),  # nf-fa-cloud
    Icon("azure", "Azure", "\U000f0825 ", (CAT_CLOUD,)),           # nf-md-microsoft_azure
    Icon("terraform", "Terraform", "\U000f1062 ", (CAT_CLOUD,)),   # nf-md-terraform
    Icon("terraform_alt", "Terraform (alt)", "\ue0a3 ", (CAT_CLOUD,)),
    Icon("nix", "Nix", "\U000f1105 ", (CAT_CLOUD,)),               # nf-md-nix
    Icon("ansible", "Ansible", "\uf195 ", (CAT_CLOUD,)),          # nf-fa-building
    Icon("helm", "Helm", "\uf298 ", (CAT_CLOUD,)),                # nf-fa-ship
    Icon("cloud", "Cloud", "\uf0c2 ", (CAT_CLOUD,)),              # nf-fa-cloud
    Icon("server_alt", "Server (alt)", "\U000f051b ", (CAT_CLOUD,)),  # nf-md-server
    Icon("shield", "Shield", "\uf132 ", (CAT_CLOUD,)),            # nf-fa-shield

    # -- Shell -------------------------------------------------------------------
    Icon("prompt_arrow", "Prompt arrow", "\u276f ", (CAT_SHELL,)),  # ❯ — standard Unicode, not Nerd Font
    Icon("prompt_dollar", "Dollar sign", "$ ", (CAT_SHELL,)),
    Icon("prompt_lambda", "Lambda", "\u03bb ", (CAT_SHELL,)),      # λ — standard Unicode
    Icon("prompt_star", "Star prompt", "\uf005 ", (CAT_SHELL,)),   # nf-fa-star
    Icon("prompt_bang", "Bang", "! ", (CAT_SHELL,)),
    Icon("jobs", "Background jobs (sparkle)", "\uf005 ", (CAT_SHELL,)),  # nf-fa-star
    Icon("jobs_alt", "Background jobs (gear)", "\uf013 ", (CAT_SHELL,)),  # nf-fa-cog
    Icon("error_x", "Error (X)", "\uf00d ", (CAT_SHELL,)),         # nf-fa-times
    Icon("error_x_circle", "Error (X in circle)", "\uf057 ", (CAT_SHELL,)),  # nf-fa-times_circle
    Icon("error_bang", "Error (warning)", "\uf071 ", (CAT_SHELL,)),  # nf-fa-warning
    Icon("error_skull", "Error (skull)", "\U000f05dc ", (CAT_SHELL,)),  # nf-md-skull
    Icon("shell_depth", "Shell depth (arrows)", "\uf0aa ", (CAT_SHELL,)),  # nf-fa-angle_double_up
    Icon("clock_alt", "Clock (alarm)", "\uf017 ", (CAT_SHELL,)),
    Icon("user", "User", "\uf007 ", (CAT_SHELL,)),                # nf-fa-user
    Icon("host", "Host / server", "\uf233 ", (CAT_SHELL,)),        # nf-fa-server
)

ICONS_BY_KEY: dict[str, Icon] = {i.key: i for i in ICONS}
ICON_CATEGORIES: tuple[str, ...] = (CAT_GENERAL, CAT_GIT, CAT_LANG, CAT_CLOUD, CAT_SHELL)


def icons_by_category() -> dict[str, list[Icon]]:
    """Group icons by category, preserving declaration order within each group."""
    grouped: dict[str, list[Icon]] = {cat: [] for cat in ICON_CATEGORIES}
    for icon in ICONS:
        for cat in icon.categories:
            if cat in grouped:
                grouped[cat].append(icon)
    return grouped


def find_icon_by_glyph(glyph: str) -> Icon | None:
    """The icon whose glyph matches, or None. Used to mark the current choice in
    the picker — a section's default icon is a raw glyph, not a catalog key."""
    for icon in ICONS:
        if icon.glyph == glyph:
            return icon
    return None
