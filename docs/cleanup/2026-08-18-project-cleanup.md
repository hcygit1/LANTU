# Project cleanup record

Date: 2026-08-18

## Removed generated data

- `jobs/`: Harbor job outputs and trial artifacts.
- `.test-tmp/`: test temporary directory.
- `.test-tmp6/`: stale test configuration fixtures.
- `.pytest_cache/`: pytest cache.

These paths contain generated data and are not required to build or run LANTU.
The generated contents were moved out of the project into the Windows temporary
directory `lantu-cleanup-20260818` because irreversible deletion is restricted
in this environment. The project no longer contains those files. An empty
`.test-tmp/` directory remains because Windows denied removing that directory.

## Removed learning artifacts

- `lessons/`: generated HTML lessons.
- `reference/`: generated HTML reference pages.
- `learning-records/`: generated Markdown learning records.
- `assets/course.css`: stylesheet used only by the removed learning pages.

These files were independent learning materials and were not referenced by the LANTU runtime.

## Preserved

- `.lantu/sessions/`: Session Journal history, including cache evaluation sessions.
- `bench/`: cache benchmark code and recorded benchmark outputs.
- `evals/harbor/`: Harbor adapter.
- `docs/`: architecture decisions, designs, plans, and research.
- `lantu/` and `tests/`: application code and tests.

The old frontend files already shown as deleted in Git status were not part of this cleanup operation.
