---
name: change-verdict
description: Verify a bug fix by running a focused pytest or JUnit-producing test against the current worktree and an ancestor Git baseline. Use before claiming a fix is complete or accepting a PR when the same test must distinguish old and new behavior.
license: MIT
metadata:
  version: "1.1.0"
---

# Change Verdict

Use this skill when the user wants evidence that a bug fix works, or when a fix is about to be reported complete. Keep the user's requested scope and repository instructions authoritative. Run project tests only in a trusted checkout or an appropriately restricted CI runner.

1. Identify the original symptom, an ancestor Git ref before the fix, a focused test file, and the exact case expected to fail on the baseline. If the symptom is unavailable, limit the conclusion to the behavior the test exercises.
2. Choose the project's documented test command. Use `--runner pytest` (default) for pytest, or `--runner junit` when the command can write JUnit XML to `{junit_xml}` or to an environment variable supplied with `--report-env`. Include a selected `--test-file` in the command. See [runner recipes](references/runners.md).
3. Resolve this Skill's installed directory, then run its `scripts/baseline_compare.py` with `--expect-case` (pytest node ID or exact JUnit case ID) and a distinctive `--expect-baseline` failure-message pattern, `--expect-exception`, or both. If the case ID or message is unknown, run once without expectations, then use the Skill's `scripts/inspect_evidence.py PATH/TO/result.json` to inspect exact case IDs and baseline messages. Choose a signature tied to the original symptom and rerun. Use `--repeat 2` when practical; `--require-candidate` turns the verdict into a CI exit status.
4. Read `result.json`, both JUnit files and UTF-8 logs, and `evidence.md`. The failure pattern is checked only against the specified case's JUnit **message**. Confirm the test assertion maps to the original symptom. Collection/setup errors, skips, ambiguous case IDs, different case sets, changed dependency/test configuration, timeouts, and working-tree changes invalidate the comparison. When config differs, inspect file status and hashes in `environment_details`; `--explore-config-diff` may show a provisional observation but never relaxes the official verdict or CI gate.
5. Report the observed result and the unverified scope. A red baseline with a green current run is candidate evidence only. A test passing on both versions does not demonstrate the change. A single run does not check stability. See [verdict meanings](references/verdicts.md).

Example command:

```text
python .agents/skills/change-verdict/scripts/baseline_compare.py --repo . --base HEAD~1 --test-file tests/test_login.py --expect-case tests/test_login.py::test_expired_token --expect-baseline "expired token" --repeat 2 --output .verdict/login -- python -m pytest -q tests/test_login.py
```

The script's default exit code reports whether evidence collection ran; `classification` reports the observation. `--require-candidate` exits 1 unless the classification is `candidate_regression_evidence`. Never claim the fix is complete merely because the script exited successfully.
