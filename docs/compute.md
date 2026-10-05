# Compute on the DNP workstations

There is no scheduler, so sharing is by convention. The pipeline helps:

1. **Choose hosts with data.** `bin/survey_hosts.sh <hosts...>` lists every
   workstation's cores, load, memory, users, container runtime and whether
   `/data/benlab` is mounted. Only hosts with `DATA=yes` and a container runtime can
   run fMRIPrep. If a host is "unreachable", set up key-based SSH to it first
   (`ssh-copy-id <host>` from dnpws26, or through the gateway).
2. **Pin your hosts.** Put 1–2 names in `compute.allowed_hosts`; every stage
   refuses to start anywhere else, so a stray terminal on the wrong machine
   cannot launch a 6-hour job.
3. **Cap resources.** `compute.n_threads`, `omp_threads` and `mem_gb` are passed
   to fMRIPrep (`--nthreads`, `--omp-nthreads`, `--mem`), and heavy jobs run
   under `nice` (`compute.nice`) so interactive users stay responsive. With
   the defaults (8 threads, 32 GB), one subject's fMRIPrep with FreeSurfer
   takes roughly 6–10 h. Run one subject at a time per host unless the host
   is idle.
4. **Survive disconnects.** `--detach` runs the stage in tmux (or nohup);
   closing VS Code or losing the SSH connection does not stop it.

Questions to settle with DNP IT / the lab:

* the workstation list and whether all of them mount `/data/benlab`;
* whether Apptainer (or Singularity) is installed everywhere, and who can
  build images (`apptainer build` from a Docker image needs no root);
* whether there is an agreed limit per user (cores/memory).
