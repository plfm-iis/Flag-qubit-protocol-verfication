# AI collaboration rules

## Authorized project

- GitHub: `plfm-iis/Flag-qubit-protocol-verfication`.
- Default working branch: `main`; verify the current remote head before work.
- Overleaf project: `6adb7e8c4019793a7dcea09f`.
- Overleaf Git remote: `https://git@git.overleaf.com/6adb7e8c4019793a7dcea09f`.
- Use only this project pairing. Do not write to a different project or repository.

## Read the latest version first

1. Read this file and `PAPER_CONTEXT.md`.
2. Run `refresh-from-overleaf.yml` before substantive manuscript reading,
   analysis, review, or editing. Updating `.github/overleaf-refresh-trigger`
   also starts this workflow.
3. Wait for success, then read the resulting GitHub `main` snapshot. Record
   its full commit SHA and the Overleaf revision in `.github/overleaf-state.json`.
4. Do not assume the previous GitHub snapshot is current. If refresh fails,
   report the failure and do not describe that snapshot as current Overleaf.
5. If GitHub contains unsynchronized manuscript changes, reconcile them with
   the user before refreshing; never overwrite them to make refresh succeed.

## Proposal, approval, and publication

- Reading relevant files, issues, history, tests, and workflows is authorized.
- Discussion, review requests, and suggestions are not manuscript write approval.
- Prepare proposed edits locally and show the **exact unified diff** before
  committing or pushing manuscript changes. State the affected files, purpose,
  and any changes to definitions, assumptions, algorithms, claims, or results.
- Obtain explicit approval for the displayed diff. Approval covers only that
  diff; unrelated cleanup or additional edits need new approval.
- Confirm substantive changes to scientific scope, correctness assumptions,
  specifications, algorithms, or interpretation with the user before applying.
- Before applying an approved diff, refresh again and compare both revisions.
  If either source changed, reread it, regenerate the diff, and obtain approval
  again. Never silently merge concurrent manuscript changes.
- Use a branch and pull request for substantive manuscript edits. Do not push
  manuscript changes merely to show a proposal.
- Apply only the approved diff and run appropriate checks or compilation.
- After the approved commit reaches `main`, create an explicit sync request
  containing the exact approved GitHub commit and expected Overleaf revision.
- Sync must stop if Overleaf changed or current GitHub paper files differ from
  the approved commit. Never force-push or resolve a conflict by overwriting.
- Report an Overleaf update only after the sync workflow has succeeded and the
  resulting state has been verified.

## Setup and maintenance metadata

The user has authorized setup and routine maintenance of the collaboration
bridge and its metadata. This includes `.github/`, `AGENTS.md`,
`PAPER_CONTEXT.md`, `CLAIMS.md`, and `.ai/`, provided the work stays within the
requested project and preserves these approval and concurrency requirements.
This authorization does not cover changes to manuscript content.

Keep this metadata on GitHub. The bridge excludes it from Overleaf publication.
Do not treat manuscript changes placed inside a metadata directory as exempt
from approval. Do not weaken the bridge checks to get a failing run to pass.

## Credentials and communication

- Use the existing Actions secret `OVERLEAF_GIT_TOKEN` for Git authentication.
- Never ask the user to paste a token into chat, print credentials, or commit
  them to the repository. If access is missing, describe the required setting.
- Discuss work in Traditional Chinese; write manuscript changes in English
  unless the user requests otherwise.
- Keep facts, verified results, and unverified assumptions distinct.
- Do not contact coauthors or other people without explicit authorization.
