# GitHub Artifact Store

Use this when Zep is unavailable. Horizon XL stores small JSON snapshots in a
GitHub repo branch through the GitHub Contents API.

## Recommended Setup

1. Create a private GitHub repo, for example `Ranikricky/horizon-xl-store`.
2. Create a branch named `horizon-artifacts`.
3. Create a fine-grained GitHub token with:
   - Repository access: only the artifact repo
   - Permissions: `Contents: Read and write`
4. Add these Render environment variables:

```bash
GIT_STORE_ENABLED=true
GIT_STORE_REPO=Ranikricky/horizon-xl-store
GIT_STORE_BRANCH=horizon-artifacts
GIT_STORE_TOKEN=github_pat_...
GIT_STORE_BASE_PATH=horizon_store
```

5. Redeploy Render.

## Notes

- This is not a database. It is a durable JSON artifact store.
- It is good for project metadata, graph snapshots, research packets, and small
  simulation state files.
- Avoid using it for very large transcripts or high-frequency writes.
- Never expose `GIT_STORE_TOKEN` in frontend code.
