#!/usr/bin/env bash
# Stage 0: report what this machine offers the pipeline. Changes nothing.
#
#   bin/check_env.sh            # paste the output back when asking for help
#
# Prints no participant data; only software versions, resources and whether the
# configured paths exist.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${ACHI_PYTHON:-python3}"
# tools installed in the same env as the chosen python (dcm2niix, dcm2bids)
PY_BIN="$(dirname "$(command -v "$PY" 2>/dev/null || echo /nonexistent/x)")"
[[ -d "$PY_BIN" ]] && export PATH="$PY_BIN:$PATH"

hr() { printf '\n== %s ==\n' "$1"; }
have() { command -v "$1" >/dev/null 2>&1; }
ver() { # ver <label> <cmd...>
    local label="$1"; shift
    if have "$1"; then printf '  %-14s %s\n' "$label" "$("$@" 2>&1 | head -1)"
    else printf '  %-14s NOT FOUND\n' "$label"; fi
}

hr "host"
echo "  hostname      $(hostname)"
echo "  os            $(. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME" || uname -sr)"
echo "  kernel        $(uname -r)"
echo "  user          $(id -un)"

hr "resources"
cores=$(nproc 2>/dev/null || echo "?")
echo "  cpu cores     ${cores}"
if have free; then
    free -g | awk '/^Mem:/ {printf "  memory        %s GB total, %s GB available\n", $2, $7}'
fi
read -r l1 l5 l15 _ </proc/loadavg 2>/dev/null && \
    echo "  load avg      ${l1} (1m) ${l5} (5m) ${l15} (15m)  on ${cores} cores"
echo "  logged in     $(who 2>/dev/null | awk '{print $1}' | sort -u | wc -l) distinct users"
echo "  busiest procs (user, %cpu, %mem, command):"
ps -eo user:12,pcpu,pmem,comm --sort=-pcpu 2>/dev/null | sed -n '2,6p' | sed 's/^/    /'
if have nvidia-smi; then
    echo "  gpu           $(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader | paste -sd';')"
fi

hr "containers"
ver apptainer apptainer --version
ver singularity singularity --version
ver docker docker --version
ver tmux tmux -V
ver screen screen --version

hr "MATLAB / SPM"
ver matlab matlab -batch "disp(version)"
if have module; then echo "  env modules   available: try 'module avail matlab'"; fi
echo "  (SPM on the server will run as a standalone container; see README stage 4)"

hr "python"
ver python3 python3 --version
ver conda conda --version
ver mamba mamba --version
"$PY" - <<'EOF' 2>/dev/null || echo "  pipeline packages not importable with $PY (create the conda env, see README)"
import importlib
for m in ["numpy", "pandas", "scipy", "nibabel", "nilearn", "pydicom", "yaml", "dcm2bids"]:
    try:
        mod = importlib.import_module(m)
        print(f"  {m:<14}{getattr(mod, '__version__', 'ok')}")
    except Exception as e:
        print(f"  {m:<14}MISSING ({e.__class__.__name__})")
EOF
ver dcm2niix dcm2niix -v
ver dcm2bids dcm2bids -v

hr "network (fMRIPrep needs TemplateFlow; container pulls need Docker Hub)"
for url in https://templateflow.s3.amazonaws.com https://registry-1.docker.io/v2/ https://pypi.org/simple/; do
    if have curl; then
        code=$(curl -s -o /dev/null -m 8 -w '%{http_code}' "$url" || echo "fail")
    else code="no curl"; fi
    printf '  %-42s %s\n' "$url" "$code"
done
echo "  (any HTTP code = reachable; 000/fail = no internet: use the offline TemplateFlow cache)"

hr "configured paths"
cd "$REPO" && PYTHONPATH="$REPO/src" "$PY" -m achi check-config 2>&1 | sed 's/^/  /'

hr "disk"
for d in /data/benlab/datalake/afch/achi "${HOME}" /tmp; do
    [[ -d "$d" ]] && df -h "$d" | awk -v d="$d" 'NR==2 {printf "  %-40s %s free of %s\n", d, $4, $2}'
done
echo
echo "done. fMRIPrep needs roughly 20-40 GB of work space per subject while running."
