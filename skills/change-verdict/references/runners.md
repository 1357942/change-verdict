# Runner recipes

The command after `--` must name at least one `--test-file`. Choose a command
already documented by the project. Run it locally once before interpreting a
baseline failure. The script passes arguments directly to the process; it does
not invoke a shell or install dependencies.

## pytest (tested)

`--runner pytest` is the default. The script appends `--junitxml=<path>`:

```text
python scripts/baseline_compare.py --repo . --base HEAD~1 --test-file tests/test_bug.py --expect-case tests/test_bug.py::test_bug --expect-baseline "assert 0 == 1" -- python -m pytest -q tests/test_bug.py
```

## Node built-in test runner (tested with Node 22)

Use generic JUnit mode. The `{junit_xml}` token is replaced with an absolute
path separately for each current and baseline run:

```text
python scripts/baseline_compare.py --repo . --base HEAD~1 --runner junit --test-file tests/booking.test.js --expect-case "test::reserves exact remaining capacity" --expect-baseline "Only 2 seats remain" --repeat 2 -- node --test --test-reporter=junit --test-reporter-destination={junit_xml} tests/booking.test.js
```

Node's JUnit reporter may use a generic `classname` such as `test`. Inspect
`current.tests.case_ids` in `result.json` to choose the exact case ID. Duplicate
case IDs make the comparison unavailable.

## Vitest (JUnit reporter recipe)

Vitest has a built-in JUnit reporter and `--outputFile` flag. If the project's
`npm test` script runs Vitest and dependencies are already installed:

```text
python scripts/baseline_compare.py --repo . --base HEAD~1 --runner junit --test-file tests/booking.test.ts --expect-case "tests/booking.test.ts::reserves exact capacity" --expect-baseline "expected" -- npm test -- --reporter=junit --outputFile={junit_xml} tests/booking.test.ts
```

Check the emitted JUnit case ID before supplying `--expect-case`; naming varies
by project and Vitest configuration. See [Vitest's reporter documentation](https://vitest.dev/guide/reporters).

## Jest with jest-junit (reporter recipe)

Jest requires an installed JUnit reporter such as `jest-junit`. The CLI sets the
`JEST_JUNIT_OUTPUT_FILE` environment variable separately for each run:

```text
python scripts/baseline_compare.py --repo . --base HEAD~1 --runner junit --report-env JEST_JUNIT_OUTPUT_FILE --test-file tests/booking.test.js --expect-case "tests/booking.test.js::reserves exact capacity" --expect-baseline "Expected" -- npm test -- --runInBand --reporters=default --reporters=jest-junit tests/booking.test.js
```

Check the actual `case_ids` and failure message in `result.json` first. The
reporter package and its configuration are project dependencies; this repository
does not install them. See [jest-junit's output-file setting](https://github.com/jest-community/jest-junit#configuration).

For any other JUnit-producing runner, use `--runner junit` with `{junit_xml}`
in one command argument, or `--report-env NAME` if its reporter reads an
environment variable. The report must contain unique `<testcase>` class/name
pairs and accurately represent failures, errors, and skips. Generic JUnit mode
is an integration contract, not a claim that every third-party reporter has
been tested.
