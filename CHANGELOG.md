# Changelog

## 1.1.0

- Added per-file dependency/test-config drift status and hashes to evidence.
- Added optional `--explore-config-diff` observations while keeping the official
  verdict and CI gate strict.
- Added a read-only evidence inspector for discovering exact case IDs and
  baseline failure messages after a first run.
- Normalized captured logs to UTF-8 and retained original bytes for non-UTF-8
  output, including Windows console encodings.

## 1.0.0

- Added generic JUnit XML runner mode, exercised with Node's built-in test runner.
- Added dynamic JUnit output paths via `{junit_xml}` or `--report-env`, enabling
  integration with Vitest and Jest reporters already installed in a project.
- Added `--require-candidate` for CI exit status while retaining evidence files.
- Rejects ambiguous JUnit case IDs and compares Python and JavaScript dependency
  and test configuration files between versions.
- Added runner recipes, CI guidance, and an explicit execution boundary.

## 0.3.0

- Bound expected failures to a selected test case and failure signature.
- Added ancestor-base checks, configuration comparison, and optional repeated runs.
