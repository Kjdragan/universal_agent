# Grant `AUTO_MERGE_PAT` "Pull requests: write" — operator runbook

**Why:** The nightly doc-accuracy sweep (`doc-nightly.yml`) now judges docs, stamps
`last_verified` on the drift-free ones, commits, and pushes a `docfix/*` branch — all
verified working. The final step, `gh pr create`, fails with:

```
pull request create failed: GraphQL: Resource not accessible by personal access token (createPullRequest)
```

That error means the **fine-grained** `AUTO_MERGE_PAT` token can push code (Contents: write)
and enable auto-merge, but is **missing the "Pull requests" permission**, so it can't *open*
the rotation PR. We can't open the PR with `GITHUB_TOKEN` instead, because a GITHUB_TOKEN-opened
PR doesn't trigger the required **"Validate PR"** check, so it could never merge.

**Fix:** add **Pull requests → Read and write** to that one token. ~60 seconds.

---

## Steps (fine-grained PAT — the expected case)

1. Open your fine-grained tokens page:
   **https://github.com/settings/tokens?type=beta**

2. Find the token whose value is stored as the repo secret `AUTO_MERGE_PAT`
   (created 2026-05-11; it's the one used by `pr-auto-merge.yml`). Click its name → **Edit**.

3. Confirm **Repository access** includes `Kjdragan/universal_agent` (it must already, since the
   token can push). If not, add it under *Only select repositories*.

4. Under **Permissions → Repository permissions**, find **Pull requests** and set it to
   **Read and write**. (Leave **Contents: Read and write** as-is — it's what makes the branch
   push work.)

5. Click **Update token** (a.k.a. *Save*) at the bottom.

   > ✅ **You do NOT need to update the GitHub Actions secret.** Editing a fine-grained token's
   > *permissions* does **not** change the token's value — only **Regenerate token** does. So the
   > existing `AUTO_MERGE_PAT` secret keeps working as-is.

---

## If it turns out to be a *classic* PAT (less likely)

Classic tokens with the `repo` scope can already create PRs, so you'd normally not see this error
with a classic token. But if the token is classic and somehow scoped too narrowly:

1. **https://github.com/settings/tokens** → click the token → ensure the **`repo`** scope is checked → **Update token**.
2. Classic tokens **cannot** have scopes edited without regenerating in some cases — if GitHub makes
   you **Regenerate**, copy the new value and update the secret:
   ```bash
   gh secret set AUTO_MERGE_PAT --repo Kjdragan/universal_agent   # paste the new token when prompted
   ```

---

## After you've granted it

Reply here with "done" (or "granted"). I'll then:

1. Re-trigger the nightly via `gh workflow run "Nightly Documentation Health"`.
2. Confirm the sweep stamps the accurate docs **and** the `docfix/*` rotation PR opens, runs
   `Validate PR`, and auto-merges to `main`.

That closes the loop — rotation fully live, no further code changes.

### (Optional) self-check before replying
If you want to confirm the grant worked yourself, this should now succeed instead of erroring:
```bash
# harmless: lists open PRs using the PAT — proves read access; create access follows the same grant
GH_TOKEN="<the PAT value>" gh pr list --repo Kjdragan/universal_agent --limit 1
```
(But you don't need to — just reply "done" and I'll verify via the real nightly run.)
