# Roadmap

## Direction

Keep the manifest, declaration, local viewer, experiment specs, reports, and
roadmap document aligned while the project stays autonomous from the Entelesis
runtime.

## Current State

- The local site and project manifest are managed from `project.yaml`.
- Experiment specs, hypotheses, runs, and analysis reports already exist inside
  the project.
- The local CLI and experiment runner are delegated project runtime, not
  Entelesis runtime.
- `docs/TODO.md` is the current project-local roadmap document.

## Target State

GStreamerExp remains a self-owned experiment project whose manifest,
declaration, specs, runs, verifiers, and local viewer all describe the same
claims and evidence.

## Migration Tracks

### Experiment Evidence

- Problem: claims can become detached from the run records and verifier results
  that make them trustworthy.
- Target: every supported claim names the hypothesis, run record, metric, and
  verifier-backed result it depends on.
- Next actions:
  - Preserve run records well enough that reports can be regenerated.
  - Keep claim language tied to hypotheses and verifier-backed evidence.
  - Keep `docs/TODO.md` linked as the active roadmap/report document.
- Completion criteria:
  - Reports can be regenerated from specs and run records.
  - Validation keeps specs and reports aligned.

### Project Management Alignment

- Problem: the manifest, declaration, local viewer, and roadmap document can
  drift when experiment structure changes.
- Target: project management reads one coherent project declaration and local
  management facts.
- Next actions:
  - Keep `PROJECT/` pages updated when specs or reports change.
  - Keep `project.yaml` operation fields limited to management facts.
- Completion criteria:
  - `python3 apps/tools/validate_project_units.py` validates the project.
  - The Project Management app shows authored roadmap content, not raw file
    names as outline structure.

## Immediate Sequence

1. Keep the manifest and declaration aligned with current experiment specs.
2. Preserve verifier-backed run records before promoting claim language.
3. Regenerate or update reports only from declared specs and records.

## Deferred Decisions

- Whether additional experiment families need nested declaration pages.
- Which project-local readiness checks should become default regression checks.
