# CI integration

Use `--require-candidate` to fail a CI job unless the expected regression is
observed. The script still writes `result.json` and logs when the verdict is not
a candidate. Configure the project-specific test file, case ID, and failure
signature before using this example.
`--explore-config-diff` only adds an unverified diagnostic observation; it never
turns a config mismatch into a passing CI verdict.

This GitHub Actions example runs on `pull_request`, checks out the PR merge
commit with its history, and passes the base SHA through an environment variable.
It grants only `contents: read` and supplies no secrets to the test process.

```yaml
name: Verify bug-fix regression
on: pull_request
permissions:
  contents: read
jobs:
  verdict:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v7
        with:
          python-version: '3.11'
      - run: python -m pip install -r requirements-dev.txt
      - name: Compare the selected regression test
        env:
          BASE_SHA: ${{ github.event.pull_request.base.sha }}
        run: |
          python .agents/skills/change-verdict/scripts/baseline_compare.py \
            --repo . --base "$BASE_SHA" \
            --test-file tests/test_bug.py \
            --expect-case tests/test_bug.py::test_bug \
            --expect-baseline 'distinctive original failure' \
            --repeat 2 --require-candidate \
            --output .verdict/bug \
            -- python -m pytest -q tests/test_bug.py
```

The repository must already contain the Skill at the shown path. Replace the
dependency-install step with the project's normal test setup. This workflow
does not post comments or upload evidence automatically. Treat PR code and
test output as untrusted; do not switch to `pull_request_target` with write
permissions or secrets while running PR code. See [GitHub's workflow
permissions](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#permissions)
and [script-injection guidance](https://docs.github.com/en/actions/concepts/security/script-injections).
