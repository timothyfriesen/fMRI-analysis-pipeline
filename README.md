# ACHI fMRI analysis pipeline

Raw Siemens DICOMs → BIDS → fMRIPrep → SPM first level → ROI results and figures,
for the ACHI task-fMRI study (affective conditioned hallucinations; early psychosis
and controls). Prisma 3T, CMRR multiband EPI, TR 1.5 s, 2 mm, 72 slices, AP.

| Stage | What it does | Runs on | Status |
|---|---|---|---|
| 00 | Environment check, host survey, config | server | ✅ |
| 01 | Series check → dcm2bids → BIDS validation | server | planned |
| 02 | fMRIPrep 25.2.6 (Apptainer/Singularity) + SDC check | server | planned |
| 03 | QC table (FD, spikes, SDC, non-steady-state) with flags | server | planned |
| 04 | Unzip MNI BOLD, smooth 6 mm, confound regressors | server (SPM container) or laptop | planned |
| 05 | Task logs → `events.tsv` + SPM onsets, HGF modulator hook | server | planned |
| 06 | First-level GLM per task + contrasts (SPM batch) | server (SPM container) or laptop | planned |
| 07 | ROI extraction → tidy CSV | server | planned |
| 08 | Figures (nilearn/matplotlib) | server | planned |

## Data policy: never commit data

This repository holds **code and configuration only**. Raw DICOMs, NIfTI files,
BIDS sidecar JSONs, derivatives, work directories, containers, `license.txt`,
behavioural logs and the sessions table (scan dates in folder names) are all
gitignored and stay on the server under `/data/benlab/datalake/afch/achi/`.
DICOM headers contain names, birth dates and scan dates, and git history is
permanent, so even a "test" subject must not be pushed. Tests use synthetic data
(`tests/synth.py`). To share what a session contains, use the de-identified
series table, which prints only series numbers, descriptions, image types, file
counts and TR/TE: `python -m achi series-table <session_folder>`.

## Setup on the server (dnpws26)

### 1. Get the code

The repository is private, so the server needs a GitHub credential once:

```bash
ssh dnpws26
# one-time: create an SSH key on the server and add the public key at
# GitHub → Settings → SSH and GPG keys
ssh-keygen -t ed25519 -C "dnpws26"
cat ~/.ssh/id_ed25519.pub
ssh -T git@github.com                     # should greet you by username

cd ~                                        # code lives in your home, data on /data
git clone git@github.com:timothyfriesen/fMRI-analysis-pipeline.git
cd fMRI-analysis-pipeline
```

Updating later: `git pull` (from inside the repo folder). Do this before each
analysis session so the server runs the latest tested code.

### 2. Python environment (conda)

```bash
conda env create -f environment.yml        # first time
conda activate achi
conda env update -f environment.yml --prune   # after requirements change
python -m pytest -q                          # all tests should pass
```

`dcm2niix` comes from the pinned `dcm2niix` PyPI wheel, so no system install is needed.

### 3. Check the machine

```bash
bin/check_env.sh | tee ~/achi_env_check.txt
```

Reports cores, memory, load, Apptainer/Singularity, tmux, Python packages,
internet access (TemplateFlow, Docker Hub) and whether the configured paths exist.

### 4. Pick 1–2 workstations (shared machines, no scheduler)

```bash
bin/survey_hosts.sh dnpws21 dnpws22 dnpws23 ...   # or: -f config/hosts.txt
```

Shows cores, 15-minute load, free memory, logged-in users, heavy CPU users,
container runtime and whether `/data/benlab` is mounted on each host. Run it at a few
times of day, then set your choice in `config/pipeline.yaml`:

```yaml
compute:
  allowed_hosts: [dnpws26]   # stages refuse to start on any other host
  n_threads: 8               # fMRIPrep --nthreads
  omp_threads: 4
  mem_gb: 32
```

See `docs/compute.md` for etiquette on shared workstations.

### 5. Configure

* `config/pipeline.yaml`: all paths and settings (committed; no hard-coded
  paths in scripts). Machine-specific overrides go in `config/pipeline.local.yaml`
  (gitignored), e.g. on your laptop.
* `config/sessions.tsv`: copy `config/sessions.example.tsv` and fill in one row
  per participant: `study_id`, `bids_label` (blank → derived: `ACHI_2026_001` →
  `ACHI001`), `session_folder` (under `paths.dicom_root`), `protocol` (`v1`, or
  `pilot` for the pilot's series naming), `group`.
* FreeSurfer license: put `license.txt` at
  `/data/benlab/datalake/afch/achi/fmriprep/license.txt` (free from
  https://surfer.nmr.mgh.harvard.edu/registration.html).

`python -m achi check-config` prints every resolved path and whether it exists.

### 6. Containers, MATLAB/SPM

Filled in with stages 02 (fMRIPrep image + offline TemplateFlow cache) and 04/06
(SPM standalone container, so the server needs no MATLAB license). Until then,
SPM steps run on your laptop.

## Running stages

```bash
bin/run_stage.sh 01 --sub ACHI001 --dry-run   # show what would happen
bin/run_stage.sh 01 --sub ACHI001             # run it
bin/run_stage.sh 02 --sub ACHI001 --detach    # long run in tmux, survives disconnects
tmux attach -t achi-02-ACHI001                #   watch it (Ctrl-b d to leave again)
bin/run_stage.sh --help                       # list stages and options
```

* **Idempotent**: a finished stage writes
  `derivatives/achi/sub-X/stamps/stage-NN.json` with a fingerprint of its inputs
  and settings; rerunning skips it unless something changed. `--force` reruns.
* **Logs**: `logs/sub-X/sub-X_stage-NN_<timestamp>.log` (full command output);
  the console shows a summary (`-v` for everything).
* **Stops loudly**: any check that fails (wrong series, missing field map, bad
  `IntendedFor`, …) stops the stage with an explanation; later stages never run on
  unchecked input.

## Tests

```bash
python -m pytest -q
```

Every stage is tested on synthetic data before it is used on real data; the
end-to-end test subject on the server is ACHI001 (and the pilot, sub-01).

## Methods (draft; versions are filled in from the outputs as stages land)

> DICOM images were converted to BIDS with dcm2bids 3.2.0 and dcm2niix
> v1.0.20250505. Preprocessing was performed with fMRIPrep 25.2.6, run in an
> Apptainer container; susceptibility distortion was corrected with a PEPOLAR
> approach using single-band reference images acquired with opposite phase
> encoding (AP/PA). Functional data were resampled to MNI152NLin2009cAsym at 2 mm.
> *[Smoothing, confounds, GLM and ROI details, and SPM version: added with stages
> 03–07.]*
