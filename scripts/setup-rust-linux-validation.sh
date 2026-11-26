#!/usr/bin/env bash
# Local development toolchain only. Never run on the production VPS.
set -euo pipefail
if ! grep -qi microsoft /proc/sys/kernel/osrelease; then
  printf '%s\n' 'This setup helper is restricted to local WSL validation.' >&2
  exit 2
fi
task_root="$HOME/.local/share/mainkraft-rust-validation"
mkdir -p "$task_root"
export CARGO_HOME="$task_root/cargo"
export RUSTUP_HOME="$task_root/rustup"
if [ ! -x "$CARGO_HOME/bin/rustup" ]; then
  python3 - "$task_root" <<'PY'
import hashlib,pathlib,sys,urllib.request
root=pathlib.Path(sys.argv[1])
url='https://static.rust-lang.org/rustup/dist/x86_64-unknown-linux-gnu/rustup-init'
with urllib.request.urlopen(url+'.sha256',timeout=30) as response:
    expected=response.read(4096).decode('ascii').split()[0]
with urllib.request.urlopen(url,timeout=30) as response:
    data=response.read(64*1024**2+1)
assert len(data)<=64*1024**2 and hashlib.sha256(data).hexdigest()==expected
path=root/'rustup-init'
path.write_bytes(data);path.chmod(0o700)
print('RUSTUP_INIT_SHA256='+expected,flush=True)
PY
  "$task_root/rustup-init" -y --no-modify-path --profile minimal --default-toolchain 1.93.0 --component clippy --component rustfmt
else
  "$CARGO_HOME/bin/rustup" toolchain install 1.93.0 --profile minimal --component clippy --component rustfmt
fi
"$CARGO_HOME/bin/rustc" +1.93.0 --version
"$CARGO_HOME/bin/cargo" +1.93.0 --version
cmake --version
pkg-config --version
