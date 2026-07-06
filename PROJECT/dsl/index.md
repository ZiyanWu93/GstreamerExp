# DSL

The project model is a layered experiment DSL: video specs, network specs, configurations, experiments, hypotheses, metric dimensions, run records, and reports compose into runnable and verifiable pipeline studies.

## Project Materials

- `specs`
- `gstexp`
- `docs/DESIGN.md`
- `docs/HYPOTHESES.md`

The DSL rule is strict: behavior-affecting fields are declared in specs and validated at load time rather than inferred silently by the runner.
