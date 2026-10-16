# ADR-0015 — One parallel (MPI) job at a time on Windows

Status: accepted (0.2.1)

## Context

Optimisations evaluate several designs at once when the cores allow (two designs of eight
cores on a sixteen-core computer). A beta tester's one-click optimisations failed for every
design after the first: the first design (today's shape) always runs alone — the
baseline-relative constraints wait for it — and the following designs run in pairs. The same
optimisations run through on Linux (OpenMPI), and with one core per design. On Windows the
bundled OpenFOAM uses Microsoft MPI, whose local jobs are started through a process manager
that `mpiexec` launches for the job; two parallel jobs started side by side on one computer
interfere (one fails or is ended with the other).

## Decision

* Execution providers declare `one_mpi_job_at_a_time`; the native Windows provider sets it.
* The job runner takes a computer-wide lock before every parallel step (an OpenFOAM step
  with more than one process) and holds it while the step runs: a named mutex
  (`Local\AeroBench-MPI`) on Windows, `flock` on a file in the temporary folder elsewhere.
  Both are released by the operating system if the runner dies. Serial steps (blockMesh,
  decomposePar, reconstructPar, one-core runs) are not affected and run side by side.
* While it waits, the job's message says so ("Waiting for another parallel run on this
  computer to finish…"), shown on the Run page and in the log; a stop or kill request ends
  the wait without starting the step.
* One-click goals plan one design at a time when runs use several cores on such a provider
  (all cores per design), so the time estimate matches.
* A failed design links to the output of its run in the design table.

## Consequences

* Optimisations on Windows evaluate parallel designs one after the other; with all cores
  per design the total time is similar (parallel efficiency of one run on all cores versus
  two on half each).
* Any combination of runs, meshes and studies started by the user is protected, not only
  optimisations.
* Tests drive the real runner with a provider that requires the lock: three parallel jobs
  never overlap, two serial ones do, and a job stopped while waiting never starts.
