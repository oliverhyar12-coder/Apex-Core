#!/usr/bin/env bash
# APEX CORE installer for Linux and macOS. No pipx needed: it creates a private
# virtual environment and links the `apex` command into ~/.local/bin.
#
#   From a clone:   bash scripts/install.sh
#   From GitHub:    curl -fsSL https://raw.githubusercontent.com/oliverhyar12-coder/Apex-Core/main/scripts/install.sh | bash
#                   (add `-s -- --ref BRANCH` after bash to install another branch)
#
# Options:  --ref BRANCH     install that branch from GitHub (default: main)
#           --source PATH    install from a local checkout or archive
#           --with-keyring   also install OS keyring support
#           --uninstall      remove the app (your config and history are kept)
# Env vars: APEX_INSTALL_DIR (venv location), APEX_BIN_DIR (default ~/.local/bin),
#           APEX_NO_MODIFY_PATH=1 (do not edit shell startup files)
set -euo pipefail

REPO_URL="${APEX_REPO_URL:-https://github.com/oliverhyar12-coder/Apex-Core}"
REF="${APEX_REF:-main}"
INSTALL_DIR="${APEX_INSTALL_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/apex-core/venv}"
BIN_DIR="${APEX_BIN_DIR:-$HOME/.local/bin}"
SOURCE=""
EXTRAS=""
UNINSTALL=0

if [ -t 1 ]; then
    BOLD=$'\033[1m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; RESET=$'\033[0m'
else
    BOLD=""; GREEN=""; YELLOW=""; RED=""; RESET=""
fi
say() { printf '%s\n' "$*"; }
info() { printf '%s==>%s %s\n' "$BOLD" "$RESET" "$*"; }
warn() { printf '%sWarning:%s %s\n' "$YELLOW" "$RESET" "$*" >&2; }
die() { printf '%sError:%s %s\n' "$RED" "$RESET" "$*" >&2; exit 1; }

usage() { sed -n '2,17p' "${BASH_SOURCE[0]:-/dev/null}" 2>/dev/null | sed 's/^# \{0,1\}//' || true; }

while [ $# -gt 0 ]; do
    case "$1" in
        --ref) [ $# -ge 2 ] || die "--ref needs a branch name"; REF="$2"; shift 2 ;;
        --ref=*) REF="${1#*=}"; shift ;;
        --source) [ $# -ge 2 ] || die "--source needs a path"; SOURCE="$2"; shift 2 ;;
        --source=*) SOURCE="${1#*=}"; shift ;;
        --with-keyring) EXTRAS="[keyring]"; shift ;;
        --uninstall) UNINSTALL=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown option: $1 (try --help)" ;;
    esac
done

# --- Uninstall -------------------------------------------------------------------

if [ "$UNINSTALL" = 1 ]; then
    link="$BIN_DIR/apex"
    if [ -L "$link" ] && [ "$(readlink "$link")" = "$INSTALL_DIR/bin/apex" ]; then
        rm -f "$link"
        info "Removed $link"
    fi
    # Only delete a directory that is clearly our virtual environment.
    if [ -n "$INSTALL_DIR" ] && [ -f "$INSTALL_DIR/pyvenv.cfg" ] && [ -x "$INSTALL_DIR/bin/apex" ]; then
        rm -rf -- "$INSTALL_DIR"
        info "Removed $INSTALL_DIR"
    else
        warn "No APEX CORE installation found at $INSTALL_DIR"
    fi
    say "Your settings and history were kept. Run \`apex config path\` before uninstalling"
    say "to see where they are, or delete ~/.config/apex-core and ~/.local/share/apex-core."
    exit 0
fi

# --- Find Python 3.11+ --------------------------------------------------------------

python_hint() {
    if [ "$(uname -s)" = "Darwin" ]; then
        say "  Install it from https://www.python.org/downloads/macos/ or with: brew install python@3.13"
        return
    fi
    local id=""
    # shellcheck disable=SC1091  # sourcing the distro's own os-release on purpose
    if [ -r /etc/os-release ]; then id="$(. /etc/os-release && echo "${ID:-} ${ID_LIKE:-}")"; fi
    case "$id" in
        *arch*|*cachyos*|*manjaro*|*endeavouros*) say "  sudo pacman -S python" ;;
        *debian*|*ubuntu*) say "  sudo apt install python3 python3-venv" ;;
        *fedora*|*rhel*|*centos*) say "  sudo dnf install python3" ;;
        *suse*) say "  sudo zypper install python3" ;;
        *) say "  Install Python 3.11 or newer with your package manager." ;;
    esac
}

PYTHON=""
for candidate in python3.14 python3.13 python3.12 python3.11 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 &&
        "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
        PYTHON="$(command -v "$candidate")"
        break
    fi
done
if [ -z "$PYTHON" ]; then
    printf '%sError:%s Python 3.11 or newer is required.\n' "$RED" "$RESET" >&2
    python_hint >&2
    exit 1
fi
info "Using $("$PYTHON" -c 'import sys; print("Python " + sys.version.split()[0])') ($PYTHON)"

# --- Work out what to install --------------------------------------------------------------

script_dir=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi
if [ -z "$SOURCE" ] && [ -n "$script_dir" ] && grep -qs 'name = "apex-core-cli"' "$script_dir/../pyproject.toml"; then
    SOURCE="$(cd "$script_dir/.." && pwd)"
fi
if [ -n "$SOURCE" ]; then
    case "$SOURCE" in
        http://*|https://*) SPEC="apex-core-cli$EXTRAS @ $SOURCE" ;;
        *)
            [ -e "$SOURCE" ] || die "--source $SOURCE does not exist"
            SPEC="$(cd "$SOURCE" && pwd)$EXTRAS"
            ;;
    esac
    info "Installing from $SOURCE"
else
    archive="$REPO_URL/archive/refs/heads/$REF.tar.gz"
    SPEC="apex-core-cli$EXTRAS @ $archive"
    info "Installing branch '$REF' from $REPO_URL"
fi

# --- Create the environment and install ------------------------------------------------------

if [ ! -x "$INSTALL_DIR/bin/python" ]; then
    info "Creating a private environment in $INSTALL_DIR"
    mkdir -p "$(dirname "$INSTALL_DIR")"
    if ! "$PYTHON" -m venv "$INSTALL_DIR" >/dev/null 2>&1; then
        rm -rf -- "$INSTALL_DIR"
        printf '%sError:%s could not create a virtual environment.\n' "$RED" "$RESET" >&2
        say "  On Debian/Ubuntu install the venv module: sudo apt install python3-venv" >&2
        exit 1
    fi
fi
VENV_PY="$INSTALL_DIR/bin/python"
"$VENV_PY" -m pip install --quiet --disable-pip-version-check --upgrade pip
info "Installing APEX CORE and its dependencies (this can take a minute)"
"$VENV_PY" -m pip install --quiet --disable-pip-version-check --upgrade "$SPEC"
# Same version number but newer code (e.g. a branch): make sure the app itself is refreshed.
"$VENV_PY" -m pip install --quiet --disable-pip-version-check --force-reinstall --no-deps "$SPEC"
"$INSTALL_DIR/bin/apex" --version >/dev/null || die "the installed apex command does not run"

mkdir -p "$BIN_DIR"
ln -sf "$INSTALL_DIR/bin/apex" "$BIN_DIR/apex"
info "Linked $BIN_DIR/apex"

# --- PATH ------------------------------------------------------------------------------

path_changed=0
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *)
        if [ "${APEX_NO_MODIFY_PATH:-0}" = 1 ]; then
            warn "$BIN_DIR is not on your PATH; add it to run \`apex\` directly."
        else
            shell_name="$(basename "${SHELL:-sh}")"
            if [ "$shell_name" = "fish" ] && command -v fish >/dev/null 2>&1; then
                fish -c "fish_add_path -U '$BIN_DIR'" && path_changed=1 && info "Added $BIN_DIR to fish's PATH"
            else
                case "$shell_name" in
                    zsh) rc="$HOME/.zshrc" ;;
                    bash) if [ "$(uname -s)" = "Darwin" ]; then rc="$HOME/.bash_profile"; else rc="$HOME/.bashrc"; fi ;;
                    *) rc="$HOME/.profile" ;;
                esac
                if ! grep -qsF "$BIN_DIR" "$rc"; then
                    # shellcheck disable=SC2016  # $PATH must stay literal in the rc file
                    printf '\n# Added by the APEX CORE installer\nexport PATH="%s:$PATH"\n' "$BIN_DIR" >> "$rc"
                    info "Added $BIN_DIR to PATH in $rc"
                fi
                path_changed=1
            fi
        fi
        ;;
esac

version="$("$INSTALL_DIR/bin/apex" --version)"
say ""
say "${GREEN}${BOLD}Installed ${version}.${RESET}"
if [ "$path_changed" = 1 ]; then
    say "Open a new terminal (so PATH is updated), then run:  ${BOLD}apex${RESET}"
else
    say "Run:  ${BOLD}apex${RESET}   (the first run walks you through setup)"
fi
say "Update later by running this installer again; remove with --uninstall."
