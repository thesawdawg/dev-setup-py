## v1.10.0 (2026-07-04)

### Feat

- **catalog**: add ansible and ansible-vault tools

## v1.9.1 (2026-07-01)

### Fix

- correct lmstudio tool entry and add homebrew

## v1.9.0 (2026-07-01)

### Feat

- remove eza tool and update llm-checker configuration in tools.yaml

## v1.8.2 (2026-07-01)

### Fix

- bump workflow silently dropped the tag after lockfile sync

## v1.8.1 (2026-07-01)

### Fix

- move GH_PAT presence check out of step if: condition
- harden bump workflow and close CI/removal gaps found in review

## v1.8.0 (2026-06-28)

### Feat

- **catalog**: add ipython to tools
- add bat and pre-commit to tool catalog

### Fix

- update git push command to ensure tags are pushed correctly
- update Pi Coding Agent tool configuration for improved installation and usage
- enhance Makefile with run-tests options for improved testing flexibility
- remove legacy migration and correct YAML refactor bugs

## v1.7.1 (2026-06-27)

### Fix

- include whichllm and corrected uv.lock

## v1.7.0 (2026-06-27)

### Feat

- load tools from YAML catalogs

## v1.6.0 (2026-06-26)

### Feat

- update add_cmd to support uvx package type and enhance GenericTool for uv dependencies

## v1.5.0 (2026-06-26)

### Feat

- post-add remove script validation for bash tools

### Fix

- restore zip permissions explicitly and surface aws installer errors
- resolve CI install failures for aws, htop, php, ruby

## v1.4.0 (2026-06-19)

### Feat

- add eza builtin tool

## v1.3.0 (2026-06-19)

### Feat

- gate tool availability on required dependencies

## v1.2.0 (2026-06-19)

### Feat

- add docker-compose.yml to dev/ sandbox
- add dev/ sandbox with Dockerfile for local install testing
- add docs command to open tool documentation in the browser
- add Go, Java, and Ruby builtin tools under languages category
- add gh, mkcert, ollama, and pi-coding-agent builtin tools
- add --verbose/-v flag to stream install/remove output

### Docs

- add Prerequisites section to README

### Chore

- add commitizen dev dep and unify version source

## v1.1.0 (2026-06-18)

### Feat

- add aws-cli and saml2aws built-in packages
- add bash type for multi-step custom installs
- add help_cmd field to all tools, display in list output
- enhance project metadata in pyproject.toml

### Refactor

- improve maintainability and extensibility for new tool additions
- switch from uv-run to pip/pipx install pattern

### Docs

- add comprehensive README

## v1.0.0 (2026-06-17)

### Feat

- initial Python implementation of dev-setup CLI
