# Security and execution boundary

`change-verdict` executes the test command you provide on the current working
tree and a temporary Git worktree at the selected baseline. A Git worktree is
not a sandbox. Tests can read files, access the network, start processes, and
modify data available to the invoking user.

Run it only on repositories and test commands you trust, or inside an
unprivileged CI runner without secrets or write credentials. Review a PR's test
command before running it locally. The script does not invoke a shell, fetch
dependencies, post comments, or upload artifacts by itself.

Evidence may include source paths, exception messages, and test output. Review
logs before sharing them. The working-tree fingerprint excludes ignored files
and generated caches, so it is an attribution aid rather than a full integrity
or environment attestation.

Report security issues privately to the repository maintainer once a public
repository exists. Do not include secrets in a public issue.
