#!/usr/bin/env python3
"""Collect a scoped before/after test comparison without editing the user's tree."""

from __future__ import annotations

import argparse
import hashlib
import json
import locale
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
import xml.etree.ElementTree as ET


GENERATED_PARTS = {".verdict", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
ENV_FILE_PATTERNS = ("pyproject.toml", "poetry.lock", "uv.lock", "Pipfile.lock",
                     "requirements*.txt", "pytest.ini", "tox.ini", "setup.cfg",
                     "package.json", "package-lock.json", "pnpm-lock.yaml",
                     "yarn.lock", "bun.lock", "vitest.config.*", "jest.config.*")


def invoke(argv: list[str], cwd: Path, *, timeout: int | None = None,
           input_bytes: bytes | None = None,
           env: dict[str, str] | None = None) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        argv, cwd=cwd, input=input_bytes, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, timeout=timeout, check=False, env=env,
    )


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    # Git warnings on stderr must never be mixed into a binary patch on stdout.
    result = subprocess.run(["git", *args], cwd=repo, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, check=False)
    if result.returncode:
        raise ValueError((result.stderr or result.stdout).decode("utf-8", "replace").strip())
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True, help="Git repository to check")
    parser.add_argument("--base", required=True, help="Git commit or branch before the fix")
    parser.add_argument("--runner", choices=("pytest", "junit"), default="pytest",
                        help="pytest injects --junitxml; junit uses {junit_xml} in the command")
    parser.add_argument("--report-env", help="For junit mode, environment variable receiving XML output path")
    parser.add_argument("--test-file", action="append", required=True,
                        help="Relative test file to apply to the baseline; repeat as needed")
    parser.add_argument("--expect-baseline", help="Regex for the intended baseline failure")
    parser.add_argument("--expect-case", help="Pytest node ID or JUnit case ID expected to fail on baseline")
    parser.add_argument("--expect-exception", help="Expected exception class for that case, optionally qualified")
    parser.add_argument("--repeat", type=int, default=1,
                        help="Run each version 1-5 times to detect unstable outcomes (default: 1)")
    parser.add_argument("--require-candidate", action="store_true",
                        help="Exit 1 unless classification is candidate_regression_evidence")
    parser.add_argument("--explore-config-diff", action="store_true",
                        help="Report a provisional observation when config differs; never relax the verdict or CI gate")
    parser.add_argument("--output", type=Path, help="Evidence directory; defaults under repo/.verdict")
    parser.add_argument("--timeout", type=int, default=120, help="Seconds allowed for each test run")
    parser.add_argument("command", nargs=argparse.REMAINDER,
                        help="Test command after --, e.g. -- python -m pytest -q tests/test_bug.py")
    args = parser.parse_args()
    if args.timeout < 1 or args.timeout > 3600:
        parser.error("--timeout must be between 1 and 3600 seconds")
    if args.repeat < 1 or args.repeat > 5:
        parser.error("--repeat must be between 1 and 5")
    if args.base.startswith("-"):
        parser.error("--base must not start with '-' ")
    if args.expect_baseline:
        try:
            re.compile(args.expect_baseline)
        except re.error as exc:
            parser.error(f"invalid --expect-baseline regex: {exc}")
    if args.expect_exception and not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*",
                                                   args.expect_exception):
        parser.error("--expect-exception must be an exception class name")
    if args.report_env and not re.fullmatch(r"[A-Za-z_]\w*", args.report_env):
        parser.error("--report-env must be an environment variable name")
    if args.report_env and args.runner != "junit":
        parser.error("--report-env is only valid with --runner junit")
    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    if not args.command:
        parser.error("provide a test command after --")
    if args.runner == "pytest":
        command_names = [Path(part).name.lower() for part in args.command]
        if not (any(name in {"pytest", "pytest.exe"} for name in command_names)
                or any(args.command[i] == "-m" and args.command[i + 1] == "pytest"
                       for i in range(len(args.command) - 1))):
            parser.error("pytest runner requires a pytest command after --")
    elif not any("{junit_xml}" in part for part in args.command) and not args.report_env:
        parser.error("junit runner requires {junit_xml} in the command or --report-env")
    return args


def normalize_case_id(value: str) -> str:
    """Convert a pytest file node ID to the JUnit class/name representation."""
    parts = value.replace("\\", "/").split("::")
    if len(parts) >= 2 and parts[0].endswith(".py"):
        module = parts[0][:-3].removeprefix("./").replace("/", ".")
        return module + ("." + ".".join(parts[1:-1]) if len(parts) > 2 else "") + "::" + parts[-1]
    return value


def exception_name(failure: ET.Element) -> str | None:
    declared = failure.get("type")
    if declared:
        return declared
    message = (failure.get("message") or "").strip().splitlines()
    first = message[0] if message else ""
    match = re.match(r"([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*):", first)
    if match:
        return match.group(1)
    if first.startswith("assert "):
        return "AssertionError"
    body_matches = re.findall(r"^E\s+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*):",
                              failure.text or "", re.MULTILINE)
    return body_matches[-1] if body_matches else None


def parse_junit(xml_path: Path, exit_code: int | None) -> tuple[dict, str, list[dict]]:
    """Use testcase elements rather than output text to distinguish test phases."""
    empty = {"total": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0,
             "case_ids": [], "failed_case_ids": [], "skipped_case_ids": []}
    if not xml_path.is_file():
        return empty, "missing_report", []
    try:
        root = ET.parse(xml_path).getroot()
    except ET.ParseError:
        return empty, "invalid_report", []
    cases = list(root.iter("testcase"))
    if not cases:
        suite_errors = sum(int(suite.get("errors", "0")) for suite in root.iter("testsuite"))
        return empty, "collection_or_setup_error" if suite_errors else "no_tests_collected", []
    counts = dict(empty)
    failures: list[dict] = []
    for case in cases:
        case_id = f"{case.get('classname', '')}::{case.get('name', '')}"
        counts["case_ids"].append(case_id)
        counts["total"] += 1
        failure = case.find("failure")
        error = case.find("error")
        skipped = case.find("skipped")
        if error is not None:
            counts["errors"] += 1
            counts["failed_case_ids"].append(case_id)
        elif failure is not None:
            counts["failed"] += 1
            counts["failed_case_ids"].append(case_id)
            failures.append({"case_id": case_id,
                             "message": failure.get("message", "") or "",
                             "exception": exception_name(failure)})
        elif skipped is not None:
            counts["skipped"] += 1
            counts["skipped_case_ids"].append(case_id)
        else:
            counts["passed"] += 1
    suite_errors = sum(int(suite.get("errors", "0")) for suite in root.iter("testsuite"))
    if len(counts["case_ids"]) != len(set(counts["case_ids"])):
        state = "ambiguous_case_ids"
    elif counts["errors"] or suite_errors:
        state = "collection_or_setup_error"
    elif counts["skipped"]:
        state = "incomplete"
    elif counts["failed"] and exit_code == 1:
        state = "failed"
    elif counts["passed"] == counts["total"] and exit_code == 0:
        state = "passed"
    else:
        state = "inconclusive_run"
    return counts, state, failures


def command_run(command: list[str], cwd: Path, timeout: int, log: Path,
                xml_path: Path, runner: str, report_env: str | None) -> dict:
    started = time.monotonic()
    if runner == "pytest":
        executed = [*command, f"--junitxml={xml_path}"]
    else:
        executed = [part.replace("{junit_xml}", str(xml_path)) for part in command]
    environment = None
    if report_env:
        environment = os.environ.copy()
        environment[report_env] = str(xml_path)
    xml_path.unlink(missing_ok=True)
    try:
        result = invoke(executed, cwd, timeout=timeout, env=environment)
        raw = result.stdout
        exit_code = result.returncode
        tests, state, failures = parse_junit(xml_path, exit_code)
    except FileNotFoundError as exc:
        raw = str(exc).encode("utf-8", "replace")
        exit_code = None
        state = "command_missing"
        tests, failures = None, []
    except subprocess.TimeoutExpired as exc:
        raw = (exc.stdout or b"") + b"\n[change-verdict] command timed out\n"
        exit_code = None
        state = "timeout"
        tests, failures = None, []
    log_text, source_encoding, lossy = decode_output(raw)
    log.write_text(log_text, encoding="utf-8")
    raw_log = None
    raw_path = log.with_suffix(log.suffix + ".raw")
    raw_path.unlink(missing_ok=True)
    if source_encoding != "utf-8" or lossy:
        raw_path.write_bytes(raw)
        raw_log = str(raw_path)
    return {
        "state": state,
        "exit_code": exit_code,
        "duration_seconds": round(time.monotonic() - started, 3),
        "log": str(log),
        "log_source_encoding": source_encoding,
        "log_decode_lossy": lossy,
        "raw_log": raw_log,
        "junit_xml": str(xml_path) if xml_path.is_file() else None,
        "tests": tests,
        "failures": failures,
        "executed_command": executed,
        "report_env": report_env,
    }


def decode_output(raw: bytes) -> tuple[str, str, bool]:
    """Write readable UTF-8 logs while retaining non-UTF-8 source bytes."""
    try:
        return raw.decode("utf-8"), "utf-8", False
    except UnicodeDecodeError:
        encoding = locale.getpreferredencoding(False) or "utf-8"
        try:
            return raw.decode(encoding), encoding, False
        except (UnicodeDecodeError, LookupError):
            return raw.decode(encoding, "replace"), encoding, True


def workspace_fingerprint(repo: Path, head_sha: str, output: Path) -> str:
    """Fingerprint tracked diff and non-ignored untracked files, excluding run artifacts."""
    digest = hashlib.sha256(head_sha.encode("ascii"))
    digest.update(git(repo, "diff", "--binary", "--no-ext-diff", "HEAD", "--").stdout)
    try:
        output_relative = output.relative_to(repo)
    except ValueError:
        output_relative = None
    names = git(repo, "ls-files", "--others", "--exclude-standard", "-z").stdout
    for raw_name in sorted(name for name in names.split(b"\0") if name):
        name = raw_name.decode("utf-8", "surrogateescape")
        relative = Path(name)
        if any(part in GENERATED_PARTS for part in relative.parts):
            continue
        if output_relative and (relative == output_relative or output_relative in relative.parents):
            continue
        file_path = repo / relative
        if file_path.is_file():
            digest.update(raw_name + b"\0")
            with file_path.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
    return digest.hexdigest()


def environment_hashes(repo: Path) -> dict[str, str]:
    """Hash common Python dependency/test config files, normalizing line endings."""
    files = {path for pattern in ENV_FILE_PATTERNS for path in repo.glob(pattern)
             if path.is_file()}
    return {path.name: hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
            for path in sorted(files)}


def environment_details(current: dict[str, str], baseline: dict[str, str]) -> list[dict]:
    """Summarize config drift without copying potentially sensitive file contents."""
    details = []
    for name in sorted(current.keys() | baseline.keys()):
        if current.get(name) == baseline.get(name):
            continue
        status = "changed" if name in current and name in baseline else (
            "added_in_current" if name in current else "missing_in_current"
        )
        details.append({"file": name, "status": status,
                        "baseline_sha256": baseline.get(name),
                        "current_sha256": current.get(name)})
    return details


def resolved_executable(command: list[str], repo: Path) -> str:
    executable = Path(command[0])
    if executable.is_absolute():
        return str(executable.resolve())
    candidate = (repo / executable).resolve()
    if candidate.is_file():
        return str(candidate)
    return shutil.which(command[0]) or command[0]


def run_signature(run: dict) -> tuple:
    tests = run.get("tests") or {}
    return (run["state"], tuple(sorted(tests.get("case_ids", []))),
            tuple(sorted(tests.get("failed_case_ids", []))),
            tuple(sorted(tests.get("skipped_case_ids", []))),
            tuple(sorted((item["case_id"], item.get("exception") or "")
                         for item in run.get("failures", []))))


def runs_consistent(runs: list[dict]) -> bool:
    return bool(runs) and all(run_signature(run) == run_signature(runs[0])
                              for run in runs[1:])


def normalize_test_files(repo: Path, names: list[str]) -> list[str]:
    found: list[str] = []
    for name in names:
        candidate = (repo / name).resolve()
        try:
            relative = candidate.relative_to(repo)
        except ValueError as exc:
            raise ValueError(f"test file escapes repository: {name}") from exc
        if not candidate.is_file():
            raise ValueError(f"test file does not exist: {name}")
        normalized = relative.as_posix()
        if normalized not in found:
            found.append(normalized)
    return found


def apply_test_changes(repo: Path, base_tree: Path, base: str,
                       test_files: list[str]) -> tuple[str, str]:
    patch = git(repo, "diff", "--binary", "--no-ext-diff", base, "--", *test_files).stdout
    patch_hash = hashlib.sha256(patch).hexdigest()
    if patch:
        applied = invoke(["git", "apply", "--binary", "--whitespace=nowarn", "-"],
                         base_tree, input_bytes=patch)
        if applied.returncode:
            raise ValueError("test patch could not be applied: " +
                             applied.stdout.decode("utf-8", "replace").strip())

    # git diff does not include untracked test files. Copy only explicitly named files.
    for name in test_files:
        untracked = git(repo, "ls-files", "--others", "--exclude-standard", "--", name)
        if name in untracked.stdout.decode("utf-8", "replace").splitlines():
            dest = base_tree / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                raise ValueError(f"untracked test would overwrite baseline file: {name}")
            shutil.copy2(repo / name, dest)
            patch_hash = hashlib.sha256((patch_hash + name +
                                         hashlib.sha256((repo / name).read_bytes()).hexdigest()
                                         ).encode()).hexdigest()
    return patch_hash, "applied"


def classify(current_runs: list[dict], baseline_runs: list[dict],
             expected_case: str | None, expected: str | None,
             expected_exception: str | None, workspace_changed: bool,
             environment_differences: list[str]) -> tuple[str, str]:
    current, baseline = current_runs[0], baseline_runs[0]
    if workspace_changed:
        return "comparison_unavailable", "working tree changed during the run"
    if not runs_consistent(current_runs) or not runs_consistent(baseline_runs):
        return "flaky_or_unstable", "repeated runs produced different states, cases, or exception types"
    if current["state"] == "failed":
        return "current_failed", "selected test failed on the current tree"
    if current["state"] != "passed":
        return "comparison_unavailable", f"current run state: {current['state']}"
    if baseline["state"] not in {"passed", "failed"}:
        return "comparison_unavailable", f"baseline run state: {baseline['state']}"
    if sorted(current["tests"]["case_ids"]) != sorted(baseline["tests"]["case_ids"]):
        return "comparison_unavailable", "current and baseline collected different test cases"
    if environment_differences:
        return "comparison_unavailable", "dependency/test config differs: " + ", ".join(environment_differences)
    if baseline["state"] == "passed":
        return "no_baseline_discrimination", "selected cases passed on both versions"
    if not expected_case or not (expected or expected_exception):
        return "baseline_failure_unclassified", "supply --expect-case and a failure pattern or exception"
    case_id = normalize_case_id(expected_case)
    if case_id not in current["tests"]["case_ids"]:
        return "comparison_unavailable", f"expected case was not collected: {expected_case}"

    def matches(run: dict) -> bool:
        for failure in run["failures"]:
            if failure["case_id"] != case_id:
                continue
            if expected and not re.search(expected, failure["message"], re.MULTILINE | re.IGNORECASE):
                continue
            found = failure.get("exception") or ""
            if expected_exception and not (found == expected_exception or
                                           found.endswith("." + expected_exception)):
                continue
            return True
        return False

    matches_by_run = [matches(run) for run in baseline_runs]
    if all(matches_by_run):
        return "candidate_regression_evidence", "expected case failed with the requested signature on every baseline run"
    if any(matches_by_run):
        return "flaky_or_unstable", "expected failure signature appeared only in some baseline runs"
    return "baseline_failure_unclassified", "baseline failed, but not with the expected case/signature"


def evidence_markdown(data: dict) -> str:
    baseline = data["baseline"]
    command = " ".join(data["command"])
    rows = ""
    for label, runs in (("Current", data["current_runs"]),
                        ("Baseline", data["baseline_runs"])):
        for number, run in enumerate(runs, 1):
            cases = run["tests"]["total"] if run["tests"] else 0
            log_name = Path(run["log"]).name
            rows += (f"| {label} #{number} | {run['state']} | {cases} | "
                     f"{run['exit_code']} | {run['duration_seconds']}s | "
                     f"[{log_name}]({log_name}) |\n")
    environment_lines = "".join(
        f"| {item['file']} | {item['status']} | "
        f"{item['baseline_sha256'] or '—'} | {item['current_sha256'] or '—'} |\n"
        for item in data["environment_details"]
    )
    if not environment_lines:
        environment_lines = "| None | Same recorded files | — | — |\n"
    provisional = data["provisional_observation"]
    provisional_line = (
        f"**Exploratory observation (not accepted):** `{provisional['classification']}` — "
        f"{provisional['reason']}  \n" if provisional else ""
    )
    return (
        "# Change verdict evidence\n\n"
        f"**Classification:** `{data['classification']}`  \n"
        f"**Reason:** {data['classification_reason']}  \n"
        f"**Base:** `{data['base_sha']}`  \n"
        f"**Current HEAD:** `{data['head_sha']}`"
        f"{' (working tree has changes)' if data['dirty'] else ''}  \n"
        f"**Working tree SHA-256:** `{data['working_tree_fingerprint']}`  \n"
        f"**Workspace changed during run:** `{data['workspace_changed_during_run']}`  \n"
        f"**Captured:** {data['captured_at']}  \n"
        f"**Runner:** `{data['runner']}`  \n"
        f"**Command:** `{command}`  \n"
        f"**Runs per version:** {data['repeat_count']}  \n"
        f"**Expected case:** `{data['expected_case']}`  \n"
        f"**Expected exception:** `{data['expected_exception']}`  \n"
        f"**Environment differences:** {', '.join(data['environment_differences']) or 'none'}  \n"
        f"**Test executable:** `{data['execution_context']['test_executable']}`  \n"
        f"**Dependency installation verified:** `False`  \n"
        f"{provisional_line}"
        f"**Selected tests:** {', '.join(data['test_files'])}  \n"
        f"**Test patch SHA-256:** `{data['test_patch_sha256']}`\n\n"
        "| Version | State | Cases | Exit | Duration | Log |\n"
        "| --- | --- | ---: | ---: | ---: | --- |\n"
        f"{rows}\n"
        "## Dependency and test configuration\n\n"
        "File hashes show what changed; they do not establish which packages were installed.\n\n"
        "| File | Status | Baseline SHA-256 | Current SHA-256 |\n"
        "| --- | --- | --- | --- |\n"
        f"{environment_lines}\n"
        "**Baseline failed cases:** " +
        (", ".join(baseline["tests"]["failed_case_ids"]) if baseline["tests"] else "unavailable") + "\n\n"
        "**Interpretation limit:** A red baseline is useful only if its failure "
        "corresponds to the original bug, not setup or collection. A green current "
        "test covers only the behavior it asserts. Inspect both logs before claiming "
        "the fix is complete.\n"
    )


def main() -> int:
    args = parse_args()
    repo = args.repo.resolve()
    if not repo.is_dir():
        print(f"repository not found: {repo}", file=sys.stderr)
        return 2
    try:
        root = Path(git(repo, "rev-parse", "--show-toplevel").stdout.decode().strip()).resolve()
        if root != repo:
            raise ValueError(f"--repo must be the Git root: {root}")
        base_sha = git(repo, "rev-parse", "--verify", f"{args.base}^{{commit}}").stdout.decode().strip()
        head_sha = git(repo, "rev-parse", "HEAD").stdout.decode().strip()
        ancestor = subprocess.run(["git", "merge-base", "--is-ancestor", base_sha, head_sha],
                                  cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  check=False)
        if ancestor.returncode == 1:
            raise ValueError("--base must be an ancestor of current HEAD")
        if ancestor.returncode:
            raise ValueError(ancestor.stderr.decode("utf-8", "replace").strip())
        dirty = bool(git(repo, "status", "--porcelain").stdout)
        test_files = normalize_test_files(repo, args.test_file)
        command_targets = [arg.replace("\\", "/").removeprefix("./") for arg in args.command]
        if not any(arg == name or arg.startswith(name + "::")
                   for name in test_files for arg in command_targets):
            raise ValueError("pytest command must target at least one selected --test-file")
    except ValueError as exc:
        print(f"change-verdict: {exc}", file=sys.stderr)
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = (args.output if args.output else repo / ".verdict" / stamp).resolve()
    if output == repo or output == repo / ".git" or repo / ".git" in output.parents:
        print("change-verdict: unsafe output path", file=sys.stderr)
        return 2
    fingerprint_before = workspace_fingerprint(repo, head_sha, output)
    current_environment = environment_hashes(repo)
    output.mkdir(parents=True, exist_ok=True)
    current_runs = [command_run(args.command, repo, args.timeout,
                                output / ("current.log" if i == 0 else f"current.{i + 1}.log"),
                                output / ("current.junit.xml" if i == 0 else f"current.{i + 1}.junit.xml"),
                                args.runner, args.report_env)
                    for i in range(args.repeat)]
    current = current_runs[0]

    baseline: dict = {
        "state": "unavailable", "exit_code": None, "duration_seconds": 0.0,
        "log": str(output / "baseline.log"), "junit_xml": None,
        "tests": None, "failures": [], "executed_command": None, "report_env": args.report_env,
    }
    baseline_runs: list[dict] = [baseline]
    patch_hash = hashlib.sha256(b"").hexdigest()
    baseline_environment: dict[str, str] = {}
    temp_root = Path(tempfile.mkdtemp(prefix="change-verdict-"))
    base_tree = temp_root / "base"
    worktree_added = False
    try:
        added = invoke(["git", "worktree", "add", "--detach", str(base_tree), base_sha], repo)
        if added.returncode:
            raise ValueError("could not create baseline worktree: " +
                             added.stdout.decode("utf-8", "replace").strip())
        worktree_added = True
        patch_hash, _ = apply_test_changes(repo, base_tree, args.base, test_files)
        baseline_environment = environment_hashes(base_tree)
        baseline_runs = [command_run(args.command, base_tree, args.timeout,
                                     output / ("baseline.log" if i == 0 else f"baseline.{i + 1}.log"),
                                     output / ("baseline.junit.xml" if i == 0 else f"baseline.{i + 1}.junit.xml"),
                                     args.runner, args.report_env)
                         for i in range(args.repeat)]
        baseline = baseline_runs[0]
    except (ValueError, OSError) as exc:
        (output / "baseline.log").write_text(str(exc) + "\n", encoding="utf-8")
    finally:
        if worktree_added:
            # The only recursive removal target is the worktree created in this temp root.
            if base_tree.resolve().is_relative_to(temp_root.resolve()):
                invoke(["git", "worktree", "remove", "--force", str(base_tree)], repo)
        if temp_root.resolve().name.startswith("change-verdict-"):
            shutil.rmtree(temp_root, ignore_errors=True)

    fingerprint_after = workspace_fingerprint(repo, head_sha, output)
    workspace_changed = fingerprint_before != fingerprint_after
    environment_differences = sorted(name for name in current_environment.keys() | baseline_environment.keys()
                                     if current_environment.get(name) != baseline_environment.get(name))
    config_details = environment_details(current_environment, baseline_environment)
    classification, classification_reason = classify(
        current_runs, baseline_runs, args.expect_case, args.expect_baseline,
        args.expect_exception, workspace_changed, environment_differences)
    provisional_observation = None
    if args.explore_config_diff and classification_reason.startswith("dependency/test config differs:"):
        provisional_classification, provisional_reason = classify(
            current_runs, baseline_runs, args.expect_case, args.expect_baseline,
            args.expect_exception, workspace_changed, [])
        provisional_observation = {
            "classification": provisional_classification,
            "reason": provisional_reason,
            "environment_unverified": True,
        }
    data = {
        "schema_version": 4,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "repo": str(repo), "base_ref": args.base, "base_sha": base_sha,
        "head_sha": head_sha, "dirty": dirty, "command": args.command,
        "runner": args.runner, "report_env": args.report_env,
        "working_tree_fingerprint": fingerprint_before,
        "working_tree_fingerprint_after": fingerprint_after,
        "workspace_changed_during_run": workspace_changed,
        "current_environment_files": current_environment,
        "baseline_environment_files": baseline_environment,
        "environment_differences": environment_differences,
        "environment_details": config_details,
        "execution_context": {
            "test_executable": resolved_executable(args.command, repo),
            "tool_python": sys.executable,
            "dependency_installation_verified": False,
        },
        "provisional_observation": provisional_observation,
        "test_files": test_files, "test_patch_sha256": patch_hash,
        "expected_baseline_pattern": args.expect_baseline,
        "expected_case": args.expect_case,
        "expected_exception": args.expect_exception,
        "repeat_count": args.repeat,
        "platform": platform.platform(), "python": sys.version.split()[0],
        "current": current, "baseline": baseline,
        "current_runs": current_runs, "baseline_runs": baseline_runs,
        "classification": classification, "classification_reason": classification_reason,
    }
    (output / "result.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                                        encoding="utf-8")
    (output / "evidence.md").write_text(evidence_markdown(data), encoding="utf-8")
    print(f"{classification}: {output / 'evidence.md'}")
    return 1 if args.require_candidate and classification != "candidate_regression_evidence" else 0


if __name__ == "__main__":
    raise SystemExit(main())
