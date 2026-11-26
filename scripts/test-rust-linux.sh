#!/usr/bin/env bash
# Offline native component tests in local WSL; no deployed services or broker.
set -euo pipefail
if ! grep -qi microsoft /proc/sys/kernel/osrelease; then
  printf '%s\n' 'This helper is restricted to local WSL validation.' >&2
  exit 2
fi
task_root="$HOME/.local/share/mainkraft-rust-validation"
export CARGO_HOME="$task_root/cargo"
export RUSTUP_HOME="$task_root/rustup"
export CARGO_TARGET_DIR="$task_root/target"
export CARGO_BUILD_JOBS=2
project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
"$CARGO_HOME/bin/rustc" +1.93.0 --version
"$CARGO_HOME/bin/cargo" +1.93.0 --version
uname -sm
exec "$CARGO_HOME/bin/cargo" +1.93.0 test --locked --manifest-path "$project_root/apps/backend-rust/Cargo.toml" --workspace --all-features --no-fail-fast
