# Project context

## Project mapping

| Item | Value |
| --- | --- |
| GitHub repository | `plfm-iis/Flag-qubit-protocol-verfication` |
| GitHub branch | `main` |
| Overleaf project | `https://www.overleaf.com/project/6adb7e8c4019793a7dcea09f` |
| Overleaf Git remote | `https://git@git.overleaf.com/6adb7e8c4019793a7dcea09f` |
| Initial GitHub import | `ec17228b7f9c31903211eb1decdb28c15d2efbcc` |
| Actions credential | `OVERLEAF_GIT_TOKEN` |

This pairing was explicitly selected by the user on 2026-10-23 (Asia/Taipei).
Overleaf is the source of the current manuscript. GitHub is the interface for
AI review and explicitly approved manuscript changes.

The organization's existing `OVERLEAF_GIT_TOKEN` is available to public
repositories. This repository was initially private, which blocked the first
connection check; the user then made it public during setup. If its
visibility changes to private in the future, verify secret availability;
the current organization plan does not expose organization secrets to private
repositories, so a repository secret with the same name would be required.
The token must have access to the specific Overleaf project above. Never put
its value in chat or source files.

The initial GitHub import contains 14 files, including `samplepaper.tex`,
`appendix.tex`, `old.tex`, `old2.tex`, two syndrome-extraction TeX files,
bibliographies, and LNCS support files. No substantive manuscript review was
performed during bridge setup. Confirm the configured root document before
compiling; a filename alone is not proof of the Overleaf root setting.

## Setup status

The active target was explicitly confirmed by the user at
2026-10-23T17:56:34Z: `6adb7e8c4019793a7dcea09f`.
The previous destination `6adb7d2d9036071907960b2e` is a separate copy
and must no longer be used for future synchronization.
Access to the active target was successfully verified. Migration checks
its exact revision and all paper files before changing the baseline.
The only approved manuscript changes are the minimum-weight YFC note
and enabling YFC comments; the formula itself is unchanged.
Consult `.github/overleaf-state.json` and the successful workflow logs
for the verified source revision and synchronization status.

## Refresh before reading

Run `.github/workflows/refresh-from-overleaf.yml` manually, or commit a unique
timestamp/message to `.github/overleaf-refresh-trigger` on `main`.
Wait for a successful run and retrieve the new `main` commit before reading.
The verified source revision and file hashes are recorded in
`.github/overleaf-state.json` by the bridge, not guessed by the assistant.

The first refresh requires the GitHub manuscript snapshot to match Overleaf
exactly before establishing a baseline. Later refreshes refuse to overwrite
GitHub manuscript edits that have not been synchronized.

## Publish an approved change

Follow `AGENTS.md`: refresh, propose an exact unified diff, obtain approval,
refresh and compare again, apply only the approved diff, and validate it.
After explicit approval and the final version check, commit the approved
manuscript changes directly to `main` and synchronize them. Branches and pull
requests are optional when the user specifically asks for them.

After the approved manuscript commit is on `main`, write
`.github/overleaf-sync-request.json` using this schema:

```json
{
  "schema": 1,
  "expected_overleaf_revision": "FULL_OVERLEAF_SHA_FROM_VERIFIED_BASELINE",
  "approved_github_commit": "FULL_GITHUB_SHA_CONTAINING_APPROVED_PAPER_FILES",
  "dry_run": false
}
```

The approved commit precedes the request's metadata commit. The current paper
snapshot must still equal that approved commit. Changing the request starts
`.github/workflows/sync-paper-to-overleaf.yml`; it can also run manually.
A missing request or a request with `dry_run: true` performs a check without
writing to Overleaf. A request is not evidence of user approval by itself;
the assistant must already have approval for the exact diff in the conversation.

Both workflows share a concurrency group. A changed Overleaf revision blocks
publication, even when the changes appear unrelated. No force push or automatic
conflict resolution is permitted. Confirm the successful workflow and updated
baseline before reporting completion.

A completed sync request is not replayable: the successful sync advances the
baseline, so an old request is rejected. Use a new verified baseline and a new
approved request for later manuscript edits. GitHub and Overleaf cannot be
updated atomically; if a run reports partial success, inspect the reported
Overleaf revision and both remotes before attempting any further write.

## Scope and existing Overleaf integration

The bridge uses the Overleaf Git remote above. It does not edit Overleaf's
built-in GitHub association. Keep collaboration metadata on GitHub and use
these workflows for bridge operations, so the recorded baseline remains useful.
Only the explicitly selected project is a publication destination.
