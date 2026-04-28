# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Workflow: commit and push after every edit (mandatory)

**As you work in this repo you MUST commit and push regularly so no work is ever lost.** This is a hard requirement, not a suggestion. Every successful file edit (Edit / Write / NotebookEdit) inside this working tree must be followed — in the same response, before yielding back to the user — by:

1. `git add <edited files>`
2. `git commit -m "<clean imperative message>"`
3. `git push` (to `origin/main`)

The goal: at any point the user can stop the session, walk away, and find every change preserved on GitHub. If you have edited a file and have not yet pushed, your turn is not finished.

**Commit message rules:**
- Imperative mood, specific about *what* changed: "Add user auth helper", "Fix typo in README intro", "Refactor parser to handle empty input".
- Never generic placeholders like "auto commit", "update file", "changes", "wip".
- Describe the change, not the task ("Fix off-by-one in pagination" — not "Fix bug user reported").

**Granularity:**
- One logical change per commit. Don't batch unrelated edits.
- Multiple files edited as part of a single logical change can share one commit.
- Many small commits is the desired outcome — granular history is the whole point.

**When NOT to commit:**
- The edit failed (don't commit broken state — fix first, then commit).
- The user explicitly said "don't commit yet" / "hold off pushing" / similar — honor that until they release the hold.
- The path is outside this working tree (e.g., `~/.claude/...` user config).
- The file is gitignored — `.claude/` is per-user Claude Code state and is not committed.

**If a push fails** (network, auth, conflict): surface the error to the user immediately. Do not silently move on; an unpushed commit means work is at risk.

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
