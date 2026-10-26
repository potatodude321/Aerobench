# Security

## Reporting a vulnerability

Please do **not** open a public issue for a security problem. Use GitHub's private reporting
instead: on the repository page, open **Security → Report a vulnerability**. You will get an
answer within a week; the fix and an advisory are published together once a fixed version is
available.

## What is in scope

* The AeroBench desktop application and its engine service.
* The Windows installer and how it installs and updates components.

The engine is a local service: it listens on `127.0.0.1` only and every request needs a token
created for each launch of the application. Problems that let another program or another user
on the same computer reach it without that token are in scope.

Vulnerabilities in the bundled third-party programs (OpenFOAM, ParaView, CalculiX,
Microsoft MPI, the embedded Python and its packages) should be reported to their projects;
tell us as well if AeroBench ships an affected version, so we can update it.

## Supported versions

Only the latest release receives fixes.
