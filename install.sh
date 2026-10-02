#!/bin/sh
# entroclaw installer for Linux and macOS.
#
#   curl -fsSL https://raw.githubusercontent.com/dev-sajid007/entroclaw/main/install.sh | sh
#
# Options (pass after `sh -s --` when piping):
#   --version X.Y.Z     install a specific release (default: latest; or set ENTROCLAW_VERSION)
#   --local             build and install from this source checkout instead of downloading
#   --no-modify-path    don't add the install directory to your shell profile
#   --uninstall         remove entroclaw (keeps your config and sessions)
#
# Environment:
#   ENTROCLAW_INSTALL_DIR   where the binary goes (default: ~/.entroclaw/bin)
#   ENTROCLAW_RELEASE_BASE  base URL of release assets (default: GitHub Releases; used by tests and mirrors)
set -eu

REPO="dev-sajid007/entroclaw"
INSTALL_DIR="${ENTROCLAW_INSTALL_DIR:-$HOME/.entroclaw/bin}"
VERSION="${ENTROCLAW_VERSION:-}"
MODIFY_PATH=1
MODE=release

if [ -t 1 ]; then BOLD="$(printf '\033[1m')"; RED="$(printf '\033[31m')"; GREEN="$(printf '\033[32m')"; RESET="$(printf '\033[0m')"; else BOLD=""; RED=""; GREEN=""; RESET=""; fi
info() { printf '%s\n' "$*"; }
ok() { printf '%s✓%s %s\n' "$GREEN" "$RESET" "$*"; }
fail() { printf '%serror:%s %s\n' "$RED" "$RESET" "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || fail "$1 is required"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --version) [ $# -ge 2 ] || fail "--version needs a value"; VERSION="$2"; shift 2 ;;
    --version=*) VERSION="${1#*=}"; shift ;;
    --local) MODE=local; shift ;;
    --no-modify-path) MODIFY_PATH=0; shift ;;
    --uninstall) MODE=uninstall; shift ;;
    -h|--help) sed -n '2,16p' "$0" 2>/dev/null || true; exit 0 ;;
    *) fail "unknown option: $1" ;;
  esac
done
VERSION="${VERSION#v}"

download() { # url dest
  case "$1" in
    file://*) cp "${1#file://}" "$2" ;;
    *) curl -fsSL --retry 3 -o "$2" "$1" ;;
  esac
}

sha256() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
  else shasum -a 256 "$1" | cut -d' ' -f1; fi
}

detect_target() {
  os="$(uname -s)"; arch="$(uname -m)"
  case "$os" in
    Linux) os=linux ;;
    Darwin) os=darwin ;;
    *) fail "unsupported OS: $os (on Windows use install.ps1)" ;;
  esac
  case "$arch" in
    x86_64|amd64) arch=x64 ;;
    arm64|aarch64) arch=arm64 ;;
    *) fail "unsupported architecture: $arch" ;;
  esac
  # Rosetta: prefer the native arm64 build on Apple silicon.
  if [ "$os" = darwin ] && [ "$arch" = x64 ] && [ "$(sysctl -n sysctl.proc_translated 2>/dev/null || echo 0)" = 1 ]; then arch=arm64; fi
  musl=""
  if [ "$os" = linux ] && [ "$arch" = x64 ] && { ldd --version 2>&1 | grep -qi musl; } ; then musl="-musl"; fi
  echo "$os-$arch$musl"
}

ensure_uv() {
  if command -v uv >/dev/null 2>&1; then UV="$(command -v uv)"; return; fi
  for candidate in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
    if [ -x "$candidate" ]; then UV="$candidate"; return; fi
  done
  info "Installing uv (Python package manager, https://docs.astral.sh/uv/)…"
  curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
  UV="$HOME/.local/bin/uv"
  [ -x "$UV" ] || fail "uv installation failed; install it manually and re-run"
}

add_to_path() {
  case ":$PATH:" in *":$INSTALL_DIR:"*) return ;; esac
  [ "$MODIFY_PATH" = 1 ] || { info "Add $INSTALL_DIR to your PATH."; return; }
  line="export PATH=\"$INSTALL_DIR:\$PATH\""
  shell_name="$(basename "${SHELL:-sh}")"
  case "$shell_name" in
    fish)
      profile="$HOME/.config/fish/config.fish"; line="fish_add_path $INSTALL_DIR" ;;
    zsh) profile="${ZDOTDIR:-$HOME}/.zshrc" ;;
    bash) if [ "$(uname -s)" = Darwin ]; then profile="$HOME/.bash_profile"; else profile="$HOME/.bashrc"; fi ;;
    *) profile="$HOME/.profile" ;;
  esac
  mkdir -p "$(dirname "$profile")"
  if ! grep -qs "$INSTALL_DIR" "$profile"; then
    printf '\n# entroclaw\n%s\n' "$line" >> "$profile"
    ok "added $INSTALL_DIR to PATH in $profile (restart your shell or: $line)"
  fi
}

uninstall() {
  rm -f "$INSTALL_DIR/entroclaw"
  rmdir "$INSTALL_DIR" "$(dirname "$INSTALL_DIR")" 2>/dev/null || true
  if command -v uv >/dev/null 2>&1; then uv tool uninstall entroclaw-agent >/dev/null 2>&1 || true
  elif [ -x "$HOME/.local/bin/uv" ]; then "$HOME/.local/bin/uv" tool uninstall entroclaw-agent >/dev/null 2>&1 || true; fi
  ok "entroclaw removed. Your settings remain in ~/.config/entroclaw and sessions in ~/.local/state/entroclaw."
  info "Remove the PATH line mentioning $INSTALL_DIR from your shell profile if one was added."
}

install_release() {
  need curl; need tar
  target="$(detect_target)"
  base="${ENTROCLAW_RELEASE_BASE:-https://github.com/$REPO/releases}"
  if [ -z "$VERSION" ]; then
    case "$base" in
      https://github.com/*) VERSION="$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" | sed -n 's/.*"tag_name": *"v\{0,1\}\([^"]*\)".*/\1/p' | head -n1)" ;;
    esac
    [ -n "$VERSION" ] || fail "could not determine the latest version; pass --version"
  fi
  case "$base" in
    https://github.com/*) asset_base="$base/download/v$VERSION" ;;
    *) asset_base="$base/v$VERSION" ;;
  esac
  archive="entroclaw-$target.tar.gz"
  case "$target" in
    *-musl) ls /usr/lib/libstdc++.so.6* >/dev/null 2>&1 || fail "musl builds need libstdc++ and libgcc: apk add libstdc++ libgcc" ;;
  esac
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT

  info "Installing ${BOLD}entroclaw $VERSION${RESET} ($target)…"
  download "$asset_base/$archive" "$tmp/$archive" || fail "download failed: $asset_base/$archive"
  download "$asset_base/SHA256SUMS" "$tmp/SHA256SUMS" || fail "download failed: $asset_base/SHA256SUMS"
  expected="$(grep " $archive\$" "$tmp/SHA256SUMS" | cut -d' ' -f1)"
  [ -n "$expected" ] || fail "$archive is not listed in SHA256SUMS"
  [ "$(sha256 "$tmp/$archive")" = "$expected" ] || fail "checksum mismatch for $archive; refusing to install"
  ok "checksum verified"

  tar -xzf "$tmp/$archive" -C "$tmp"
  mkdir -p "$INSTALL_DIR"
  install -m 0755 "$tmp/entroclaw" "$INSTALL_DIR/entroclaw"
  ok "installed $INSTALL_DIR/entroclaw"

  ensure_uv
  wheel="entroclaw_agent-$VERSION-py3-none-any.whl"
  case "$asset_base" in
    file://*) spec="${asset_base#file://}/$wheel" ;;
    *) spec="$asset_base/$wheel" ;;
  esac
  "$UV" tool install --force --quiet --python 3.12 "$spec" || fail "installing the agent failed"
  ok "installed the agent (entroclaw-agent) with uv"
}

install_local() {
  need bun
  src="$(cd "$(dirname "$0")" && pwd)"
  [ -f "$src/cli/package.json" ] && [ -f "$src/agent/pyproject.toml" ] || fail "--local must be run from a source checkout"
  info "Building entroclaw from $src…"
  (cd "$src/cli" && bun install --frozen-lockfile >/dev/null && bun run build >/dev/null)
  mkdir -p "$INSTALL_DIR"
  install -m 0755 "$src/cli/dist/entroclaw" "$INSTALL_DIR/entroclaw"
  ok "installed $INSTALL_DIR/entroclaw"
  ensure_uv
  "$UV" tool install --force --quiet --reinstall --python 3.12 "$src/agent" || fail "installing the agent failed"
  ok "installed the agent (entroclaw-agent) with uv"
}

case "$MODE" in
  uninstall) uninstall; exit 0 ;;
  local) install_local ;;
  *) install_release ;;
esac

add_to_path
info ""
info "${BOLD}Done.${RESET} Next:"
info "  entroclaw auth              # add an API key"
info "  cd your-project && entroclaw"
