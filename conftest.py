"""Repository-wide pytest settings."""

# evaluation_runs/ holds pinned worktrees of this repository for paid runs;
# collecting their duplicate tests would break the suite.
collect_ignore = ["evaluation_runs"]
