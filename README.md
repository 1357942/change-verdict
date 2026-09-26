# change-verdict

`change-verdict` is an Agent Skill and CLI for checking whether a bug fix has meaningful regression-test evidence. It runs an explicitly chosen test on the current tree and a temporary Git worktree at the baseline, saves structured reports and logs, and produces a scoped evidence card.

The first case it catches: a new test is green after the fix **and was already green before the fix**. That test may be useful, but it does not demonstrate the fix.

## Scope

Version 1.1 supports pytest directly and any test runner that writes JUnit XML to a specified path. Node's built-in test runner is covered by integration tests. Vitest and Jest can use the generic JUnit mode when their project already has a JUnit reporter configured. The tool makes a scoped evidence classification; it does not decide that every path of a fix works.

## Use as a Skill

Install the [Skill directory](skills/change-verdict/) into an Agent Skills directory (for example `.agents/skills/change-verdict/` in a project), or use `npx skills add 1357942/change-verdict --skill change-verdict`. The Skill follows the [Agent Skills directory format](https://agentskills.io/specification). Invoke it when reviewing a bug fix, or ask the agent to verify the fix before completion. Python 3.10+, Git, and the selected project's test runner are required; the CLI itself has no third-party Python dependencies.

## Run the comparison directly

```text
python skills/change-verdict/scripts/baseline_compare.py --repo /path/to/repo --base main --test-file tests/test_bug.py --expect-case tests/test_bug.py::test_reproduction --expect-exception AssertionError --repeat 2 --output /path/to/repo/.verdict/bug -- python -m pytest -q tests/test_bug.py
```

`--expect-case` accepts a pytest node ID or exact JUnit case ID. A candidate verdict also needs `--expect-baseline` (matched only against that case's JUnit failure **message**) or `--expect-exception` (the exception class), or both. Without these, a red baseline and green current run are reported as unclassified. The command must target at least one named `--test-file`. `--repeat` accepts 1–5 runs per version and defaults to 1; use 2 or more to detect unstable outcomes. `--require-candidate` exits 1 for every other classification while still writing evidence.

For Node, Vitest, and Jest command examples, read [runner recipes](skills/change-verdict/references/runners.md). For a read-only GitHub Actions workflow, read [CI integration](skills/change-verdict/references/ci.md).

### First run: identify the exact case

If you do not know the JUnit case ID or baseline failure message, run the comparison without `--expect-case` and `--expect-baseline`, then inspect its JSON:

```text
python skills/change-verdict/scripts/inspect_evidence.py /path/to/output/result.json
```

The inspector shows current case IDs and each baseline failure's case ID, exception, and short message preview. Confirm that the failing assertion corresponds to the original bug, then rerun with an exact `--expect-case` and distinctive failure signature. The inspector only reads the evidence file; it does not run tests.

### When dependency or test configuration differs

The official classification remains `comparison_unavailable`. `environment_details` in `result.json` and the evidence card list each added, missing, or changed file with hashes; file contents are not copied into the report. Use `--explore-config-diff` only for diagnosis: it adds `provisional_observation` after the ordinary test checks, marked `environment_unverified`. Even if that observation is a red/green candidate, the official classification and `--require-candidate` exit status remain unchanged. Inspect the actual dependency diff and installed environment before drawing a conclusion.

The output directory contains `result.json`, `current.log`, `baseline.log`, `current.junit.xml`, `baseline.junit.xml`, and `evidence.md` when both runs emit reports. Logs are UTF-8; when a test runner emits another encoding, the original bytes are also saved as `*.log.raw` and the source encoding is recorded. Repeated runs add numbered logs and JUnit files. A successful script exit means evidence collection completed; inspect `classification` and logs for the verdict. The comparison uses a temporary Git worktree and removes it afterward. Current uncommitted product code is included in the current run, and the selected test changes are copied as a patch into the baseline worktree. A working-tree fingerprint records the tracked diff and non-ignored untracked files and detects changes during the run.

## Limits

- The selected test file must exist in the current tree. If it depends on new production APIs or other changed test helpers, the baseline may be unable to collect it; this is reported as unavailable.
- The script records the Python runtime, platform, and common Python/JavaScript dependency and test-config hashes. A detected config difference makes the comparison unavailable. Equal hashes cannot guarantee that installed dependencies, services, or test data are identical across worktrees.
- Log decoding prefers UTF-8 and falls back to the host's preferred encoding. For unusual mixed-encoding output, inspect the preserved raw log before sharing evidence.
- Skipped tests, collection or setup errors, different test case sets, and a workspace that changes during execution make the comparison unavailable.
- Repeated runs expose some unstable cases but cannot prove a test is never flaky. A matching case and failure signature are candidate evidence that still needs inspection against the original bug.
- The script runs the selected project's test command in both trees. Git worktrees are **not sandboxes**; use a trusted repository or restricted CI runner without secrets. See [security notes](skills/change-verdict/SECURITY.md).
- The script does not edit product code or create Git commits. Tests themselves may have side effects.

## Test

```text
python -m unittest discover -s tests -v
```
