#!/usr/bin/env bash
# Run one pipeline stage for one subject.
#
#   bin/run_stage.sh 01 --sub ACHI001 [--dry-run] [--force] [-v] [--detach]
#
# --detach  start the stage in a tmux session (or nohup if tmux is missing) so it
#           survives an SSH / VS Code disconnect. Reattach with:
#             tmux attach -t achi-<stage>-<sub>
# Every other option is passed to `python -m achi run`; see `bin/run_stage.sh --help`.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="${REPO}/src${PYTHONPATH:+:${PYTHONPATH}}"
PY="${ACHI_PYTHON:-python3}"

if [[ $# -lt 1 || "$1" == "-h" || "$1" == "--help" ]]; then
    sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
    echo
    "$PY" -m achi stages
    exit 0
fi

detach=0
args=()
for a in "$@"; do
    if [[ "$a" == "--detach" ]]; then detach=1; else args+=("$a"); fi
done

if [[ $detach -eq 0 ]]; then
    exec "$PY" -m achi run "${args[@]}"
fi

# --- detached run --------------------------------------------------------
stage="${args[0]}"
sub="unknown"
for ((i = 1; i < ${#args[@]}; i++)); do
    [[ "${args[$i]}" == "--sub" ]] && sub="${args[$((i + 1))]:-unknown}"
done
name="achi-${stage}-${sub#sub-}"
# Write a small launcher script so no shell quoting has to survive tmux/nohup.
launcher="${TMPDIR:-/tmp}/${name}-$(id -un).sh"
{
    echo "#!/usr/bin/env bash"
    printf 'cd %q\n' "$REPO"
    printf 'export PYTHONPATH=%q\n' "$PYTHONPATH"
    printf '%q ' "$PY" -m achi run "${args[@]}"; echo
    echo 'rc=$?; echo; echo "[stage finished with exit code $rc]"; exit $rc'
} >"$launcher"
chmod +x "$launcher"

if command -v tmux >/dev/null 2>&1; then
    if tmux has-session -t "$name" 2>/dev/null; then
        echo "tmux session '$name' already exists: is this stage still running?" >&2
        echo "  attach: tmux attach -t $name     kill: tmux kill-session -t $name" >&2
        exit 1
    fi
    # remain-on-exit keeps the window (and the exit message) after the run ends
    tmux new-session -d -s "$name" "bash $(printf '%q' "$launcher")"
    tmux set-option -t "$name" remain-on-exit on >/dev/null
    echo "started in tmux session '$name'"
    echo "  watch:  tmux attach -t $name   (detach again with Ctrl-b d)"
else
    out="${REPO}/nohup_${name}_$(date +%Y%m%d-%H%M%S).out"
    nohup bash "$launcher" >"$out" 2>&1 &
    echo "tmux not found; started with nohup (pid $!), console output in $out"
fi
echo "  full log: <project_root>/logs/sub-${sub#sub-}/"
