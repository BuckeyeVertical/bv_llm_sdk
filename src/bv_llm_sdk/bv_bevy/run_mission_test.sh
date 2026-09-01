#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

set -a
source "$repo_dir/config/mission_test.env"
set +a

cd "$repo_dir"
exec cargo run "$@"
