# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Workflow: commit and push after every edit

This repo runs on per-edit commits — a deliberate user choice for maximum revertibility. After every successful Edit/Write/NotebookEdit inside this working tree, stage the edited files, commit with a clean imperative message describing *what* changed, and push to `origin/main`.

- Don't batch unrelated edits into one commit. One logical change per commit, even if small.
- Multiple files edited as part of a single logical change can share a commit.
- Messages are imperative and specific: "Add user auth helper", "Fix typo in README intro" — never "auto commit" or "update file".
- Skip the commit only if: the edit failed, the user says "don't commit yet" / "hold off", or the path is outside this working tree.
- `.claude/` is gitignored (per-user Claude Code state); edits there are not commits.

## Git identity is repo-local

`user.name` and `user.email` are set in `.git/config` for this repo only — the user's **global** git config is intentionally empty. If a commit ever fails with a missing-identity error, restore with:

```
git config user.name "zachevansss"
git config user.email "zevans4548@gmail.com"
```

Don't promote these to `--global`.

## Remote

`origin` → `https://github.com/zachevansss/Claude-Code-Test` (private). Default branch is `main`. `gh` CLI is authenticated as `zachevansss` and can be used for repo-level operations (issues, PRs, releases).

## Project state

This is an experimentation workspace — at the time of writing there is no source code, build system, test framework, or linter configured. As real code lands, extend this file with the actual build / test / run commands and any non-obvious architecture. Until then, don't fabricate commands or workflows that aren't present in the repo.
