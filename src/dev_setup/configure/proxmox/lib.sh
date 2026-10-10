# shellcheck shell=bash
#
# Shared transport, resolution and safety helpers for the pve-* functions.
#
# This file is SOURCED FROM THE INSTALLED PACKAGE, never copied into the user's config
# directory: it and the functions that use it ship together, so there is no version of
# one that can meet a different version of the other (SD-2). Each function bootstraps
# with:
#
#     _env="$("${DEVSTUFF_BIN:-devstuff}" configure proxmox --export "${profile:-}")" || exit 1
#     eval "$_env"
#     . "$PVE_LIB" || exit 1
#
# The exported environment says where this file is, which interpreter to parse JSON
# with, and what the chosen profile's connection details are. It never carries the
# secret — only PVE_SECRET_ENV or PVE_SECRET_FILE, which are dereferenced here at the
# moment a request is built, so the secret stays out of the eval text, out of `set -x`
# traces, and out of argv (NFR-3).
#
# Nothing here calls `exit`. Helpers return a status and the caller decides, because
# `exit` inside a command substitution only ends the subshell — a trap this file would
# otherwise fall into on every `$(pve_read ...)`.

PVE_LIB_LOADED=1

# ---------------------------------------------------------------------------
# Output. Everything diagnostic goes to stderr so a function's stdout stays pipeable.
# ---------------------------------------------------------------------------

pve_err()  { printf '  %s\n' "$*" >&2; }
pve_warn() { printf '  ! %s\n' "$*" >&2; }
pve_note() { printf '  %s\n' "$*" >&2; }

# The line these functions exist to teach: the command itself, where it runs, and how.
pve_show() {
    printf '\n  → %s\n' "$1" >&2
    if [ -n "${2:-}" ]; then
        printf '    %s\n\n' "$2" >&2
    else
        printf '\n' >&2
    fi
}

pve_have() { command -v "$1" >/dev/null 2>&1; }

pve_need() {
    # pve_need COMMAND REMEDY
    if pve_have "$1"; then
        return 0
    fi
    pve_err "$1 is required but is not on your PATH."
    pve_err "  $2"
    return 1
}

# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------

# Print the profile's secret on stdout. Returns 1 and explains on stderr if it cannot
# be resolved. Only ever used inside a pipeline feeding curl's stdin, or to set SSHPASS.
pve_secret() {
    if [ -n "${PVE_SECRET_ENV:-}" ]; then
        if [ -z "${!PVE_SECRET_ENV:-}" ]; then
            pve_err "Profile '$PVE_PROFILE' expects its secret in \$$PVE_SECRET_ENV, which is empty."
            pve_err "  Set it in this shell, or re-run:  devstuff configure proxmox"
            return 1
        fi
        printf '%s' "${!PVE_SECRET_ENV}"
        return 0
    fi
    if [ -n "${PVE_SECRET_FILE:-}" ]; then
        if [ ! -r "$PVE_SECRET_FILE" ]; then
            pve_err "Profile '$PVE_PROFILE' expects its secret in $PVE_SECRET_FILE, which is not readable."
            pve_err "  Re-run:  devstuff configure proxmox"
            return 1
        fi
        # Permissions are reported, not enforced: refusing to run over a mode the user
        # chose would be devstuff overruling them about their own file.
        local mode
        mode="$(stat -c '%a' "$PVE_SECRET_FILE" 2>/dev/null || echo '')"
        case "$mode" in
            600|400|"") ;;
            *) pve_warn "$PVE_SECRET_FILE is mode $mode — other users on this machine can read it." ;;
        esac
        # A trailing newline from an editor is not part of the token.
        tr -d '\r\n' < "$PVE_SECRET_FILE"
        return 0
    fi
    pve_err "Profile '$PVE_PROFILE' has no secret_env or secret_file set."
    pve_err "  Fix it with:  devstuff configure proxmox"
    return 1
}

# ---------------------------------------------------------------------------
# Transport: SSH
# ---------------------------------------------------------------------------

# Quote arguments for the remote shell. ssh concatenates its command arguments with
# spaces and hands the result to a shell on the far side, so anything with a space in
# it (a snapshot description, most obviously) has to arrive already quoted.
#
# `printf %q` assumes the far side understands bash quoting, which a Proxmox node does —
# root's shell there is bash. The one form that would not survive a POSIX-only shell is
# the $'...' it emits for a value containing a newline.
pve_remote_cmd() {
    local out="" a
    for a in "$@"; do
        out+="$(printf '%q' "$a") "
    done
    printf '%s' "${out% }"
}

pve_ssh_prereq() {
    pve_need ssh "Install the openssh-client package:  sudo apt-get install -y openssh-client" || return 1
    if [ "${PVE_AUTH:-agent}" = "password" ]; then
        pve_need sshpass "Install it with:  sudo apt-get install -y sshpass" || return 1
    fi
    return 0
}

# Run a command on the node. Arguments are the remote argv.
pve_ssh() {
    pve_ssh_prereq || return 1

    local -a opts=(-o "ConnectTimeout=${PVE_SSH_TIMEOUT:-10}" -p "$PVE_PORT")
    # -n keeps ssh from swallowing the caller's stdin, which matters because most reads
    # happen inside $(...) while the terminal is still the confirmation prompt's.
    opts+=(-n)

    if [ -n "${PVE_IDENTITY:-}" ]; then
        opts+=(-i "$PVE_IDENTITY" -o IdentitiesOnly=yes)
    fi

    local remote
    remote="$(pve_remote_cmd "$@")"
    if [ "${PVE_SUDO:-0}" = "1" ]; then
        remote="sudo -n $remote"
    fi

    if [ "${PVE_AUTH:-agent}" = "password" ]; then
        # BatchMode would disable password auth outright. StrictHostKeyChecking=yes is
        # deliberate: the default (`ask`) would put an unanswerable host-key prompt
        # behind sshpass's pty and hang. Failing cleanly is the better half of that
        # trade, and the error names the fix.
        opts+=(-o BatchMode=no -o StrictHostKeyChecking=yes)
        local secret
        secret="$(pve_secret)" || return 1
        # A `VAR=value cmd` prefix puts the value in the child's *environment*. The
        # obvious `env "SSHPASS=$secret" sshpass ...` would put it in env's argv, which
        # is world-readable through ps for the life of the call — the exact thing
        # NFR-3 exists to prevent.
        SSHPASS="$secret" sshpass -e ssh "${opts[@]}" "$PVE_USER@$PVE_HOST" "$remote"
    else
        opts+=(-o BatchMode=yes)
        ssh "${opts[@]}" "$PVE_USER@$PVE_HOST" "$remote"
    fi
}

# The node this profile logs into, which is not necessarily the node a guest lives on.
# Cached for the life of the script; only costs a round trip when the profile never
# recorded it.
pve_local_node() {
    if [ -z "${PVE_NODE:-}" ]; then
        PVE_NODE="$(pve_ssh hostname)" || return 1
    fi
    printf '%s' "$PVE_NODE"
}

# ---------------------------------------------------------------------------
# Transport: API
# ---------------------------------------------------------------------------

# pve_api PATH [key=value ...] — a GET against /api2/json, JSON on stdout.
#
# The token reaches curl through `-K -` on stdin. Putting it in -H would publish it to
# every process on the machine through /proc/<pid>/cmdline for the life of the request.
pve_api() {
    pve_need curl "Install the curl package:  sudo apt-get install -y curl" || return 1

    local path="$1"; shift
    local url="${PVE_ENDPOINT}${path}"

    local -a args=(-sS -G --max-time "${PVE_HTTP_TIMEOUT:-25}")
    local kv
    for kv in "$@"; do
        args+=(--data-urlencode "$kv")
    done
    if [ "${PVE_VERIFY_TLS:-1}" != "1" ]; then
        args+=(-k)
    fi

    local secret
    secret="$(pve_secret)" || return 1
    # curl's config format takes a quoted value with backslash escapes, so a secret
    # containing a quote would otherwise end the header early and send a truncated
    # credential. Proxmox tokens are UUIDs today; this costs two lines and does not
    # depend on that staying true.
    secret="${secret//\\/\\\\}"
    secret="${secret//\"/\\\"}"

    local body code status
    # mktemp creates it 0600; the response body can carry as much as the token can read.
    body="$(mktemp "${TMPDIR:-/tmp}/devstuff-pve.XXXXXX")" || return 1
    code="$(printf 'header = "Authorization: PVEAPIToken=%s=%s"\n' "$PVE_TOKEN_ID" "$secret" \
        | curl "${args[@]}" -K - -o "$body" -w '%{http_code}' "$url")"
    status=$?

    if [ "$status" -ne 0 ]; then
        rm -f "$body"
        pve_err "Could not reach $PVE_HOST:$PVE_API_PORT (curl exit $status)."
        if [ "${PVE_VERIFY_TLS:-1}" = "1" ] && [ "$status" -eq 60 ]; then
            pve_err "  Its certificate was not trusted. A default Proxmox install uses a self-signed"
            pve_err "  one — set verify_tls: false for this profile, or install the cluster CA."
        fi
        return 1
    fi

    case "$code" in
        2*)
            cat "$body"
            rm -f "$body"
            return 0
            ;;
        401)
            pve_err "Proxmox rejected the token for profile '$PVE_PROFILE' (401)."
            pve_err "  Check token_id ($PVE_TOKEN_ID) and the secret it points at."
            ;;
        403)
            pve_err "The token is not permitted to read $path (403)."
            if [ -n "${PVE_PRIV_HINT:-}" ]; then
                pve_err "  It needs: $PVE_PRIV_HINT"
            fi
            pve_err "  Privileges are granted with:  pveum acl modify <path> --tokens '$PVE_TOKEN_ID' --roles <role>"
            ;;
        *)
            pve_err "Proxmox returned HTTP $code for $path."
            head -c 400 "$body" >&2 2>/dev/null
            printf '\n' >&2
            ;;
    esac
    rm -f "$body"
    return 1
}

# ---------------------------------------------------------------------------
# Transport-agnostic read
# ---------------------------------------------------------------------------

# pve_read PATH [key=value ...] — the same API path over either transport. On an ssh
# profile it goes through `pvesh`, which is the node's own API client, so both
# transports return byte-identical JSON and only one set of formatters is needed.
pve_read() {
    local path="$1"; shift
    if [ "$PVE_TRANSPORT" = "api" ]; then
        pve_api "$path" "$@"
        return $?
    fi
    local -a flags=()
    local kv
    for kv in "$@"; do
        flags+=("--${kv%%=*}" "${kv#*=}")
    done
    pve_ssh pvesh get "$path" "${flags[@]}" --output-format json
}

# The equivalent of pve_read as a line a user could type, for the "→" hint.
pve_read_cmd() {
    local path="$1"; shift
    if [ "$PVE_TRANSPORT" = "api" ]; then
        local query="" kv
        for kv in "$@"; do
            query+="${query:+&}$kv"
        done
        printf "curl -H 'Authorization: PVEAPIToken=%s=...' '%s%s%s'" \
            "$PVE_TOKEN_ID" "$PVE_ENDPOINT" "$path" "${query:+?$query}"
        return 0
    fi
    local flags="" kv
    for kv in "$@"; do
        flags+=" --${kv%%=*} ${kv#*=}"
    done
    printf 'pvesh get %s%s --output-format json' "$path" "$flags"
}

pve_fmt() {
    "$PVE_PYTHON" "$PVE_FMT" "$@"
}

# ---------------------------------------------------------------------------
# Guests
# ---------------------------------------------------------------------------

# pve_resolve GUEST — accept a name or a VMID and fill in everything else.
#
# /cluster/resources?type=vm answers cluster-wide and tags each guest qemu or lxc, so
# one request settles the VMID, the node, and which of qm/pct manages it. An ambiguous
# name is never guessed at: fmt.py lists the candidates and exits 2 (SD-9).
pve_resolve() {
    local guest="$1" json line
    json="$(pve_read "/cluster/resources" "type=vm")" || return 1
    line="$(printf '%s' "$json" | pve_fmt resolve "$guest")" || return $?

    PVE_G_VMID="$(printf '%s' "$line" | cut -f1)"
    PVE_G_KIND="$(printf '%s' "$line" | cut -f2)"
    PVE_G_NODE="$(printf '%s' "$line" | cut -f3)"
    PVE_G_NAME="$(printf '%s' "$line" | cut -f4)"
    PVE_G_STATUS="$(printf '%s' "$line" | cut -f5)"
    PVE_G_LOCK="$(printf '%s' "$line" | cut -f6)"
    case "$PVE_G_KIND" in
        qemu) PVE_G_BIN="qm" ;;
        lxc)  PVE_G_BIN="pct" ;;
        *)
            pve_err "Unexpected guest type '$PVE_G_KIND' for $guest."
            return 1
            ;;
    esac
    if [ -n "$PVE_G_LOCK" ]; then
        pve_warn "$PVE_G_NAME is locked ($PVE_G_LOCK) — Proxmox will refuse most operations."
    fi
    return 0
}

pve_guest_label() {
    printf '%s %s (%s, %s on %s)' "$PVE_G_KIND" "$PVE_G_VMID" "$PVE_G_NAME" \
        "$PVE_G_STATUS" "$PVE_G_NODE"
}

# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

# State changes go over ssh only (SD-8). The refusal names the profile and its transport
# rather than saying "unsupported", because the fix is a different profile, not a
# different command.
pve_require_ssh() {
    if [ "$PVE_TRANSPORT" = "ssh" ]; then
        return 0
    fi
    pve_err "Profile '$PVE_PROFILE' is an API profile, and this changes state on the node."
    pve_err "  devstuff only makes changes over SSH, where the command is the one you would"
    pve_err "  type on the node yourself. Reads work fine on this profile."
    pve_err "  Add an SSH profile with:  devstuff configure proxmox"
    return 1
}

# Confirmation needs stdin AND stdout to be a terminal (SD-7). stdin alone is not
# enough: `devstuff agent` runs script functions with stdout captured while stdin is
# still the REPL's terminal, and a stdin-only check would block there forever on a
# prompt written into a buffer nobody can see.
# Declining is an answer, not a failure — these two codes are how that is told apart
# from a command that went wrong, so that saying "no" does not end in a red banner.
PVE_DECLINED=10
PVE_NO_TERMINAL=11

pve_confirm() {
    local prompt="$1" reply
    if [ "${DEVSTUFF_PVE_ASSUME_YES:-}" = "1" ]; then
        pve_note "DEVSTUFF_PVE_ASSUME_YES=1 is set — running it without asking."
        return 0
    fi
    if [ ! -t 0 ] || [ ! -t 1 ]; then
        pve_err "This changes state, and there is no terminal here to confirm on."
        pve_err "  Run it from a terminal, or set DEVSTUFF_PVE_ASSUME_YES=1 to approve in advance."
        return "$PVE_NO_TERMINAL"
    fi
    read -r -p "  $prompt [y/N] " reply
    case "$reply" in
        y|Y|yes|YES|Yes) return 0 ;;
        *)
            pve_note "Nothing was run. The command above is the one to type by hand."
            return "$PVE_DECLINED"
            ;;
    esac
}

# Turn a pve_do status into the script's exit status. `devstuff run` flattens every
# non-zero code to 1 and prints "command failed", so a declined confirmation has to
# come back as 0 or answering "no" looks like a crash.
pve_finish() {
    if [ "${1:-0}" = "$PVE_DECLINED" ]; then
        exit 0
    fi
    exit "${1:-0}"
}

# pve_do PROMPT -- COMMAND... : show it, confirm it, run it on the guest's own node.
#
# qm, pct and vzdump are node-local: run `qm start 101` on pve1 for a guest that lives on
# pve2 and Proxmox answers that the config file does not exist. So when the guest is
# elsewhere in the cluster the command is wrapped in the hop an administrator would type
# themselves — and the hop is what gets shown, because it is the true command.
pve_do() {
    local prompt="$1"; shift
    [ "${1:-}" = "--" ] && shift
    pve_require_ssh || return 1

    local here
    here="$(pve_local_node)" || return 1

    local -a remote=("$@")
    if [ -n "${PVE_G_NODE:-}" ] && [ "$PVE_G_NODE" != "$here" ]; then
        remote=(ssh "$PVE_G_NODE" "$(pve_remote_cmd "$@")")
    fi

    pve_show "$(pve_remote_cmd "${remote[@]}")" "on $here, via ssh $PVE_USER@$PVE_HOST"
    pve_confirm "$prompt" || return $?
    pve_ssh "${remote[@]}"
}

# ---------------------------------------------------------------------------
# Self-test — one implementation, used by `pve-check` and by the wizard (SD-4)
# ---------------------------------------------------------------------------

_pve_result() { printf '%s\t%s\t%s\n' "$1" "$2" "$3"; }

# Emits `status<TAB>name<TAB>detail` lines and always exits 0; the caller judges.
pve_selftest() {
    local detail

    if [ "$PVE_TRANSPORT" = "ssh" ]; then
        if pve_have ssh; then
            _pve_result ok "ssh client" "$(command -v ssh)"
        else
            _pve_result fail "ssh client" "not on PATH — sudo apt-get install -y openssh-client"
            return 0
        fi
        if [ "${PVE_AUTH:-agent}" = "password" ] && ! pve_have sshpass; then
            _pve_result fail "sshpass" "password auth needs it — sudo apt-get install -y sshpass"
            return 0
        fi
    else
        if pve_have curl; then
            _pve_result ok "curl" "$(command -v curl)"
        else
            _pve_result fail "curl" "not on PATH — sudo apt-get install -y curl"
            return 0
        fi
        if [ "${PVE_VERIFY_TLS:-1}" != "1" ]; then
            _pve_result warn "TLS" "certificate verification is off for this profile"
        fi
    fi

    if [ "${PVE_AUTH:-agent}" != "password" ] && [ "$PVE_TRANSPORT" = "ssh" ]; then
        :
    elif detail="$(pve_secret 2>&1 >/dev/null)"; then
        _pve_result ok "secret" "${PVE_SECRET_ENV:+\$$PVE_SECRET_ENV}${PVE_SECRET_FILE:+$PVE_SECRET_FILE}"
    else
        _pve_result fail "secret" "${detail%%$'\n'*}"
        return 0
    fi

    local version
    if version="$(pve_read "/version" 2>&1)"; then
        _pve_result ok "connect" "$(printf '%s' "$version" | pve_fmt version 2>/dev/null || echo reachable)"
    else
        _pve_result fail "connect" "${version%%$'\n'*}"
        return 0
    fi

    if [ "$PVE_TRANSPORT" = "ssh" ]; then
        local missing
        missing="$(pve_ssh sh -c 'for b in qm pct pvesh vzdump; do command -v $b >/dev/null 2>&1 || printf "%s " "$b"; done')"
        if [ -z "$missing" ]; then
            _pve_result ok "node tools" "qm, pct, pvesh and vzdump are all present"
        else
            _pve_result fail "node tools" "missing on the node: ${missing% }"
        fi
        local node
        node="$(pve_ssh hostname)" || node=""
        if [ -n "$node" ]; then
            if [ -n "${PVE_NODE:-}" ] && [ "$PVE_NODE" != "$node" ]; then
                _pve_result warn "node name" "profile says '$PVE_NODE', the host calls itself '$node'"
            else
                _pve_result ok "node name" "$node"
            fi
        fi
    else
        # Report what this token may actually do, per read, rather than one yes/no. The
        # apt/update row is the point: it needs Sys.Modify, so a PVEAuditor token fails
        # exactly here and nowhere else.
        local node="${PVE_NODE:-}"
        if [ -z "$node" ]; then
            node="$(pve_read "/nodes" 2>/dev/null | pve_fmt first-node 2>/dev/null)" || node=""
        fi
        _pve_probe "guest inventory" "/cluster/resources" "type=vm"
        _pve_probe "cluster status" "/cluster/status"
        if [ -n "$node" ]; then
            _pve_probe "storage" "/nodes/$node/storage"
            _pve_probe "pending updates" "/nodes/$node/apt/update"
        fi
    fi
    return 0
}

_pve_probe() {
    local label="$1" path="$2"; shift 2
    local out
    if out="$(pve_api "$path" "$@" 2>&1 >/dev/null)"; then
        _pve_result ok "$label" "$path"
    elif printf '%s' "$out" | grep -q '403'; then
        _pve_result warn "$label" "403 — the token lacks the privilege for $path"
    else
        _pve_result fail "$label" "${out%%$'\n'*}"
    fi
}

# The human form of the same results, for `devstuff run pve-check`. Returns 1 if
# anything failed, so "could not perform the lookup" is a non-zero exit while a warning
# is not.
pve_check_report() {
    local failed=0 line status name detail
    while IFS=$'\t' read -r status name detail; do
        case "$status" in
            ok)   printf '  [ ok ] %-16s %s\n' "$name" "$detail" ;;
            warn) printf '  [warn] %-16s %s\n' "$name" "$detail" ;;
            *)    printf '  [FAIL] %-16s %s\n' "$name" "$detail"; failed=1 ;;
        esac
    done < <(pve_selftest)
    return "$failed"
}
