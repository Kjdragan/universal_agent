# Phase 3B: Devin PR Review Integration

**Status:** Not started. Begin only after Phase 3A (pipeline simplification) is complete and verified.
**Where this lives:** Save to `docs/pipeline/PHASE_3B_DEVIN_PR_REVIEW.md` in the repo.
**Depends on:** Phase 3A must be complete first — Devin reviews PRs into `develop`, and the simplified pipeline must be in place before adding a review layer.

---

## What this does

Adds Devin as an automated PR reviewer on pull requests targeting `develop`. Devin reviews every PR, posts comments about potential issues, and provides a second pair of eyes on every change. The review is **advisory only** — it does not block merges.

---

## Why

- You're a solo developer. Every change you make goes from your brain to production with no external review.
- Devin's review won't catch everything, but it catches some things, and the commentary is useful for debugging when things break later.
- When something goes wrong after a deploy, you can go back to the PR, read Devin's comments, and often find it flagged the exact issue.
- It's "theater" in the sense that it doesn't block anything, but it's theater with a paper trail that has real value.

---

## What gets added

One file: `.github/workflows/pr-review.yml`

That's it. No other changes to the codebase, no other workflows modified, no slash commands changed.

---

## How it works

### The flow

```
feature/latest2 → PR into develop → Devin reviews automatically → auto-merge fires on CI green → deploy via /ship
```

1. You open a PR from `feature/latest2` to `develop` (the `/ship` command does this automatically)
2. Devin's auto-review triggers on the PR `opened` / `synchronize` / `ready_for_review` events
3. Devin reads the diff, runs analysis, posts comments on the PR (takes 5–10 minutes)
4. Meanwhile, your required CI checks pass and auto-merge fires
5. The PR merges — often before Devin finishes reviewing
6. Devin's comments land on the (now closed) PR — still readable, still useful

### What Devin does NOT do

- Does not block merges (its review is not a required status check)
- Does not approve PRs (no auto-approval)
- Does not push code or modify your branch
- Does not run on the `develop → main` fast-forward (there's no PR there, just a push)

### Why non-blocking

Your velocity matters more than gate-keeping. If Devin were a required check, every deploy would wait 5–10 minutes for Devin to finish. For a solo dev, that's friction with no corresponding safety gain — you can read Devin's comments after the merge just as easily as before.

If you later decide you want Devin to be a hard gate (e.g., you add a teammate and want reviews to block), you can change the GitHub branch protection rules on `develop` to require the Devin check. That's a settings change, not a code change.

---

## Prerequisites

### Devin account and API key

1. Sign up at `app.devin.ai` if you haven't already
2. Go to Settings → API Keys → Create new key
3. Copy the key
4. Add it as a GitHub Actions secret: repo Settings → Secrets and variables → Actions → New repository secret → name it `DEVIN_API_KEY`

### Devin repo access

1. In the Devin web UI, go to Settings → Integrations → GitHub
2. Connect your GitHub account
3. Grant Devin access to `Kjdragan/universal_agent`

### Devin Review settings (you've already done part of this)

From your earlier screenshot, you already have:
- Auto review enabled on `Kjdragan/universal_agent`
- Review mode set to "Auto review"

Verify these are still in place. If the Devin Review settings page shows `Kjdragan/universal_agent` with "Auto review" selected, you're good.

---

## The workflow file

### `.github/workflows/pr-review.yml`

```yaml
name: Devin PR Review

on:
  pull_request:
    branches: [develop]
    types: [opened, synchronize, ready_for_review]

jobs:
  devin-review:
    runs-on: ubuntu-latest
    if: github.event.pull_request.draft == false
    steps:
      - name: Checkout repository
        uses: actions/checkout@v5
        with:
          fetch-depth: 0

      - name: Get changed files
        id: changed
        run: |
          FILES=$(gh pr diff ${{ github.event.pull_request.number }} --name-only | tr '\n' ' ')
          echo "files=$FILES" >> "$GITHUB_OUTPUT"
        env:
          GH_TOKEN: ${{ github.token }}

      - name: Request Devin review
        if: steps.changed.outputs.files != ''
        run: |
          curl -s -X POST "https://api.devin.ai/v1/sessions" \
            -H "Authorization: Bearer ${{ secrets.DEVIN_API_KEY }}" \
            -H "Content-Type: application/json" \
            -d '{
              "prompt": "Your tasks:\n1. Clone the repository ${{ github.repository }} locally.\n2. Set up a pre-push Git hook that prevents pushes from user \"Devin AI\" or email containing \"devin-ai-integration\".\n3. View the diffs of changed files for PR #${{ github.event.pull_request.number }} in repository ${{ github.repository }}.\n4. Run the code locally to verify changes work as expected.\n5. Read the PR discussion for previous comments.\n6. If no issues found, post a comment saying \"Everything looks good!\" and stop.\n7. Otherwise, identify issues and provide inline code comments on the diffs.\n8. Post feedback as detailed comments on the PR, referencing specific lines.",
              "idleTimeoutMins": 30
            }'
```

**Notes on the workflow:**
- The `if: github.event.pull_request.draft == false` skips draft PRs
- The `fetch-depth: 0` ensures the full diff is available
- The Devin API session takes 5–10 minutes to complete
- The workflow itself completes quickly (just fires the API call) — Devin works asynchronously
- Devin posts comments directly on the PR via the GitHub API using its own integration

### Alternative: Use Devin's built-in Auto Review instead

If you already have Devin's Auto Review enabled on the repo (which your screenshot suggests), you may not need this workflow file at all. Devin's Auto Review triggers automatically when PRs are opened — it's a Devin-side feature, not a GitHub Actions workflow.

**Check first:** Open a test PR from `feature/latest2` to `develop`. If Devin automatically starts reviewing it within a few minutes (you'll see a "Devin is reviewing" status check or comments appearing), then the Auto Review feature is handling it and you don't need `pr-review.yml`.

**If Auto Review isn't firing:** Then add the workflow file above as a manual trigger.

**If both are running:** You'll get double reviews, which wastes Devin ACUs. Disable one — either turn off Auto Review in Devin's settings, or don't add the workflow file.

---

## Scoping Devin to `develop` only

Devin should only review PRs targeting `develop`. It should NOT review:
- Direct pushes to `main` (there's no PR to review — it's a fast-forward)
- PRs targeting `main` (you shouldn't have any in the new pipeline)
- PRs between feature branches

The workflow file's `on: pull_request: branches: [develop]` handles this automatically.

If using Devin's built-in Auto Review instead of the workflow, check whether Auto Review can be scoped per base branch. If it reviews all PRs regardless of target branch, that's fine for now — the only PRs you'll have are `feature/latest2 → develop`.

---

## What to look for in Devin's reviews

Devin categorizes findings as:
- **Red (probable bugs)** — things that will likely break at runtime
- **Yellow (warnings)** — things that might cause issues under certain conditions
- **Gray (FYI/commentary)** — style suggestions, observations, non-critical notes

For a solo dev, the red findings are the ones worth reading immediately. Yellow is worth scanning. Gray is background reading for when you have time.

When something breaks after a deploy:
1. Go to the PR that introduced the change
2. Read Devin's comments
3. If Devin flagged the issue: you have a head start on the fix
4. If Devin missed it: the bug was subtle enough that automated review wouldn't have caught it anyway — no loss

---

## Rollout sequence

1. Verify Devin account is set up and has repo access
2. Add `DEVIN_API_KEY` to GitHub Actions secrets (if using the workflow approach)
3. Test with a throwaway PR: create a branch, make a trivial change, open PR to `develop`
4. Verify Devin reviews it (either via Auto Review or via the workflow)
5. If using the workflow: commit `pr-review.yml` to the repo via a normal `/ship`
6. Confirm subsequent PRs get reviewed automatically

---

## Cost considerations

Devin charges in ACUs (Agent Compute Units). Each review consumes ACUs. On the Teams plan, you get 250 ACUs/month (~62 hours of agent work). A typical PR review uses a fraction of an ACU.

If you're concerned about cost:
- Monitor your Devin usage dashboard
- Consider only enabling reviews for larger PRs (add a file-count threshold to the workflow)
- Disable Auto Review and only trigger reviews manually when you want them

For a solo dev pushing a few PRs per day, the cost is negligible relative to the plan allocation.

---

## Acceptance criteria

- [ ] Devin has repo access to `Kjdragan/universal_agent`
- [ ] Either Auto Review is enabled OR `pr-review.yml` is committed (not both)
- [ ] A test PR to `develop` receives a Devin review within 10 minutes
- [ ] The review is advisory only (does not block merge)
- [ ] Devin does NOT review direct pushes to `main`
- [ ] The owner has read at least one Devin review and understands the red/yellow/gray categorization

---

## End of Phase 3B
