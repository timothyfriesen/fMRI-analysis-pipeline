#!/usr/bin/env bash
# Survey the DNP workstations to pick 1-2 machines for the pipeline.
#
#   bin/survey_hosts.sh dnpws21 dnpws22 ...     # hosts on the command line
#   bin/survey_hosts.sh -f config/hosts.txt     # one host per line (gitignored)
#   bin/survey_hosts.sh                         # use 'Host dnp*' entries in ~/.ssh/config
#
# Needs passwordless SSH (keys) to each host; hosts that ask for a password are
# reported as "unreachable" instead of hanging. Run it at a few different times of
# day: the 15-minute load is a snapshot, not a schedule. Changes nothing remotely.
set -uo pipefail

hosts=()
if [[ "${1:-}" == "-f" ]]; then
    [[ -f "${2:-}" ]] || { echo "host file not found: ${2:-}" >&2; exit 1; }
    mapfile -t hosts < <(grep -vE '^\s*(#|$)' "$2")
elif [[ $# -gt 0 ]]; then
    hosts=("$@")
elif [[ -f "$HOME/.ssh/config" ]]; then
    mapfile -t hosts < <(awk 'tolower($1)=="host" {for (i=2;i<=NF;i++) if ($i ~ /^dnp/ && $i !~ /[*?]/) print $i}' "$HOME/.ssh/config" | sort -u)
fi
if [[ ${#hosts[@]} -eq 0 ]]; then
    echo "no hosts given. Pass hostnames, or -f <file>. Ask DNP IT for the workstation list," >&2
    echo "or look for 'Host' entries in ~/.ssh/config on your laptop." >&2
    exit 1
fi

# Runs on each host; prints one tab-separated line.
read -r -d '' REMOTE <<'EOF'
cores=$(nproc)
read -r l1 l5 l15 _ </proc/loadavg
mem=$(free -g | awk '/^Mem:/ {print $2"/"$7}')
users=$(who | awk '{print $1}' | sort -u | wc -l)
top=$(ps -eo user:20,pcpu --no-headers | awk '{c[$1]+=$2} END {for (u in c) if (c[u]>50) printf "%s:%.0f%% ", u, c[u]}')
rt=$( (command -v apptainer >/dev/null && apptainer --version) || (command -v singularity >/dev/null && singularity --version) || echo none)
data=$( [[ -d /data/benlab/datalake ]] && echo yes || echo no )
gpu=$( command -v nvidia-smi >/dev/null && nvidia-smi --query-gpu=name --format=csv,noheader | head -1 || echo - )
printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$cores" "$l1" "$l15" "$mem" "$users" "$rt" "$data" "$gpu" "${top:--}"
EOF

printf '%-12s %5s %6s %6s %6s %-11s %5s %-26s %-5s %-14s %s\n' \
    HOST CORES LOAD1 LOAD15 BUSY% MEM_GB USERS CONTAINER DATA GPU "HEAVY_USERS(>50%cpu)"
rows=()
for h in "${hosts[@]}"; do
    out=$(ssh -o BatchMode=yes -o ConnectTimeout=6 -o StrictHostKeyChecking=accept-new \
              "$h" "bash -s" <<<"$REMOTE" 2>/dev/null | tail -1)
    if [[ -z "$out" ]]; then
        printf '%-12s unreachable (no key-based SSH, or host down)\n' "$h"
        continue
    fi
    IFS=$'\t' read -r cores l1 l15 mem users rt data gpu top <<<"$out"
    busy=$(awk -v l="$l15" -v c="$cores" 'BEGIN {printf "%.0f", 100*l/c}')
    printf '%-12s %5s %6s %6s %6s %-11s %5s %-26s %-5s %-14s %s\n' \
        "$h" "$cores" "$l1" "$l15" "$busy" "$mem" "$users" "${rt:0:26}" "$data" "${gpu:0:14}" "$top"
    rows+=("$busy $h")
done

echo
echo "MEM_GB = total/available. BUSY% = 15-min load / cores."
if [[ ${#rows[@]} -gt 0 ]]; then
    echo "least busy right now: $(printf '%s\n' "${rows[@]}" | sort -n | head -2 | awk '{print $2" ("$1"%)"}' | paste -sd, -)"
fi
echo "A good pipeline host: DATA=yes, CONTAINER present, BUSY% low at several times of day,"
echo "and enough free memory for compute.mem_gb. Put your 1-2 choices in"
echo "compute.allowed_hosts (config/pipeline.yaml) so stages refuse to start elsewhere."
