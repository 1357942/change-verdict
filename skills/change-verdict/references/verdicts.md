# Interpreting a comparison

| Classification | Meaning | Appropriate statement |
| --- | --- | --- |
| `candidate_regression_evidence` | The same JUnit cases ran; current passed; the specified case failed on every baseline run with the requested failure message or exception. Dependency/test config matched. | The test distinguishes the two versions. Inspect its assertion against the original symptom before claiming the bug is fixed. |
| `no_baseline_discrimination` | Both commands passed. | This test does not show the behavior changed. It may still be valid for behavior that existed already. |
| `current_failed` | A selected case failed on the current tree. | The selected acceptance check failed on the current tree. Inspect the log. |
| `baseline_failure_unclassified` | Current passed; baseline failed without a matching expected pattern. | The baseline is red, but the reason has not been established. |
| `flaky_or_unstable` | Repeated runs differed in state, collected cases, failed cases, exception type, or expected signature. | The result is not stable enough for a red/green conclusion. |
| `comparison_unavailable` | A report is missing or invalid, test collection or setup failed, cases were skipped, ambiguous, or differed between versions, dependency/test config differed, a run timed out, or the working tree changed. | No comparable red/green conclusion is available. |

The tool does not decide whether a test failure belongs to the intended bug. It narrows matching to the specified case's JUnit failure message and optional exception class; that is evidence for review, not a semantic proof. A new test may import a production API missing from the base; that is a collection failure, not a valid red result. Likewise, a test that passes on both versions is not automatically worthless.

If dependency or test configuration differs, the official classification stays `comparison_unavailable`. With `--explore-config-diff`, `provisional_observation` records what the test outcomes would suggest without that configuration gate. It is explicitly unverified and never satisfies `--require-candidate`.

The evidence card records the base SHA, current HEAD, working-tree fingerprint, exact command, selected test files, JUnit case counts, log paths, repeat count, and a hash of the test patch. `result.json` also records common dependency/test config hashes from both trees, per-file status and hashes for differences, the resolved test executable, and the classification reason. It does not certify full environment equality. Compare installed packages, services, and pre-existing failures before drawing a delivery conclusion.
