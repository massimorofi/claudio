---
name: git-github
description: "Git and GitHub operations for Claudio - local git (commits, branches, diffs, remotes) and GitHub via the authenticated gh CLI (issues, PRs, releases, Projects V2 boards). Covers authentication from inside a Docker container: checking what is available, installing gh without root, device-flow login, and PAT/env-var setup. Use when cloning, committing, pushing, opening PRs, reading GitHub issues or project boards, or when a GitHub operation fails with an auth or 404 error."
license: MIT
---

# Git & GitHub

Two distinct surfaces: **local git** (works offline, no auth) and **GitHub**
(remote hosting: issues, PRs, releases, Projects). Local git never needs a
credential; anything that touches `github.com` does. Confusing the two is the
root of most "why is this failing" loops.

## When to use

- Cloning, branching, committing, diffing, rebasing, resolving conflicts.
- Opening / reviewing / merging pull requests.
- Reading or writing GitHub issues, releases, Actions runs.
- Reading a **GitHub Projects V2 board** (the `.../projects/N` URLs).
- A GitHub URL returns **404** or an API call returns **401/403** — almost
  always an auth problem, not a missing resource. See §3.

## When NOT to use

- The target is not GitHub (use the relevant tool directly).
- A plain local file operation already solves the task.

## 1. Local git

Local operations need no credential. The recurring wins:

```bash
git status -sb                      # short branch + porcelain changes
git diff --staged                   # what is actually about to be committed
git log --oneline --graph -n 20     # real topology, not the file list
git show <sha> --stat               # one commit, files touched
git switch -c feat/<name> origin/main
git restore <path>                  # discard working changes (newer than checkout --)
```

**Commit hygiene.** Stage deliberately (`git add -p`), never `git add .` on a
dirty tree — it sweeps in build artifacts, `.env`, and other agents' edits.
Check `git diff --staged` before every commit. If `.env`, tokens, or
`__pycache__` show up, fix `.gitignore` first.

**Never rewrite a pushed branch casually.** `git push --force` on a shared
branch destroys other people's work. Use `--force-with-lease` if you must
rewrite, and only on your own branch.

## 2. GitHub: prefer `gh` over raw HTTP

For anything on GitHub, use the **authenticated `gh` CLI**, not `curl` /
`wget` / `web_fetch`. Unauthenticated fetches hit a 60-req/hour IP rate
limit, cannot see private resources, and silently return 404 for things that
exist. `gh` uses the token, gets 5000-req/hour, and gives structured output.

```bash
gh repo view owner/repo
gh issue list --repo owner/repo --state open
gh issue view 42 --repo owner/repo
gh pr create --fill
gh pr view 123 --repo owner/repo --json state,mergeable,reviews
gh run list --repo owner/repo --limit 10
gh api repos/owner/repo/pulls                       # raw REST escape hatch
```

Rule of thumb: if you are about to type `https://api.github.com` or
`raw.githubusercontent.com` into a fetch tool, stop and use `gh` instead.

## 3. Authentication — read this before anything else

GitHub auth is the single most common failure point, and it is **worse inside
a container** because the place your token lives on the host is usually not
visible to the process. Diagnose before you assume.

### 3.1 Check what is actually available (do this first)

Never assume "the user is logged in" means "I can act". Run the probe:

```bash
# gh CLI present and authenticated?
gh auth status                      # shows account + token scopes
gh auth token                       # prints the active token (or errors)

# env-var tokens
echo "GH_TOKEN=[${GH_TOKEN}]"; echo "GITHUB_TOKEN=[${GITHUB_TOKEN}]"

# git credential helper + stored creds
git config --global credential.helper
ls -la ~/.git-credentials ~/.netrc ~/.config/gh/hosts.yml 2>/dev/null

# keyring (desktop only)
command -v secret-tool && secret-tool lookup service gh
```

If `gh auth status` says "not logged into any GitHub hosts", you have **no
auth in this environment**, regardless of what you did in a browser or on the
host. A browser session and a CLI token are completely separate things.

### 3.2 The container reality (what I learned the hard way)

When Claudio runs as a Docker container, several things that are true on the
host are **false inside**:

- **You are a different user.** The container runs as its own uid (e.g.
  `claudio`, uid 1000), **not** the host user (`briggen`). `su <hostuser>`
  fails — that user is not in the container's `/etc/passwd`.
- **No root, no sudo.** `id` shows a non-zero uid; `sudo` is absent. You
  cannot install system packages or read root-owned files.
- **The host home is NOT mounted.** Only specific paths (the project dir,
  the workspace) are bind-mounted. The host's `~/.config/gh/hosts.yml`,
  `~/.git-credentials`, `~/.netrc`, and the GNOME keyring are **outside the
  mount and invisible**. A `gh auth login` done on the host does nothing for
  the container.
- **`/var/run/docker.sock` is not mounted**, so you cannot shell out to
  `docker exec` into the host's containers either.

**Conclusion:** host-side login does not transfer. You must give the
container its own credential. Three ways, in order of preference.
### 3.3 Option A — pass a token via env var (best for containers)

A Personal Access Token injected as `GH_TOKEN` (or `GITHUB_TOKEN`) is the
cleanest container pattern: no interactive login, no keyring, works headless.

1. On the host, create a token at
   `https://github.com/settings/tokens`:
   - **Classic PAT**: tick the **`repo`** scope, and **`project`** if you
     need Projects V2 (see §3.6). `read:org` for org-scoped reads.
   - **Fine-grained PAT**: grant Contents (read), Issues (read/write),
     Pull requests (read/write), and **Projects: Read** as needed.
2. Put it in the container's environment. For this repo's compose stack, add
   to the `agent` service `environment:` block:

   ```yaml
   environment:
     GH_TOKEN: ${GH_TOKEN:?set GH_TOKEN in .env}
   ```

   and `GH_TOKEN=ghp_xxxx` in `.env`. `gh` and `git` both pick `GH_TOKEN`
   up automatically; `gh` prefers it over the stored hosts.yml.
3. Verify: `gh auth status` should now report the account and scopes.

**Single-quote JSON in `.env`** (this repo's gotcha): any JSON-valued key
must be single-quoted or bash quote-removal corrupts it. A bare token is
fine unquoted.

### 3.4 Option B — device-flow login inside the container (no root needed)

When you have no PAT and want `gh` to mint its own token, use the OAuth
**device flow**. It needs no browser inside the container — the user opens a
URL on their own machine and types a short code.

`gh` is **not installed** in a minimal image and you cannot `apt-get` it
without root. Install it into a user dir instead:

```bash
mkdir -p ~/.local/bin
TAG=$(curl -s https://api.github.com/repos/cli/cli/releases/latest \
      | python3 -c "import sys,json;print(json.load(sys.stdin)['tag_name'])")
VER=${TAG#v}
curl -sL -o /tmp/gh.tar.gz \
  "https://github.com/cli/cli/releases/download/${TAG}/gh_${VER}_linux_amd64.tar.gz"
# the tarball is prefixed gh_<ver>_linux_amd64/ ; the binary is at bin/gh
tar -xzf /tmp/gh.tar.gz -C ~/.local/bin --strip-components=2 \
  "gh_${VER}_linux_amd64/bin/gh"
export PATH="$HOME/.local/bin:$PATH"
gh --version
```

Then start the device flow. **It blocks waiting for the user**, so detach it
properly or your shell call times out:

```bash
setsid gh auth login --hostname github.com --git-protocol https --web \
  --scopes project,repo,read:org > /tmp/ghauth.log 2>&1 < /dev/null &
disown
sleep 5; cat /tmp/ghauth.log
```

The log prints a **one-time code** (e.g. `2929-F776`) and the URL
`https://github.com/login/device`. Tell the user to open that URL and enter
the code, then poll `gh auth status` until it reports logged in. The token
is written to `~/.config/gh/hosts.yml` **inside the container**.
### 3.5 Option C — bind-mount the host's gh config

If the host already has `gh` authenticated and you want to reuse it, mount
the config read-only into the container:

```yaml
volumes:
  - /home/<hostuser>/.config/gh:/home/claudio/.config/gh:ro
```

Caveats: the token is shared (audits show the host identity), and if the
host used the **keyring** backend rather than `hosts.yml`, the file alone is
not enough — re-run `gh auth login` or switch the host to file storage with
`gh auth login` choosing "Login with a token".

### 3.6 Scope requirements (match the task to the scope)

A token that authenticates fine can still be **refused** for a specific
resource because of missing scope. The non-obvious ones:

| Task | Required scope |
|---|---|
| Read/write code, PRs, issues in a repo | `repo` |
| **GitHub Projects V2** (the `.../projects/N` board) | **`project`** (classic) / Projects: Read+Write (fine-grained) |
| Org membership / teams | `read:org` / `admin:org` |
| Releases | `repo` |
| Gists | `gist` |

**Critical gotcha:** a Projects V2 board returns **404** to an
unauthenticated or under-scoped caller even when the issues in it are
public. The board and the repo have *different* visibility. If
`https://github.com/users/<owner>/projects/<n>` 404s but
`api.github.com/repos/<owner>/<repo>/issues` works, the board is private —
you need the `project` scope, not a different URL.
## 4. GitHub Projects V2 boards (GraphQL)

Projects V2 has **no REST API** — it is GraphQL only. `gh api graphql` is
the way. The board's columns are a **single-select field** named `Status`;
each item carries its value in `fieldValues`.

First, discover the board's fields (names + option lists):

```bash
gh api graphql -f query='
query { user(login:"OWNER") { projectV2(number: N) {
  title
  fields(first:25){ nodes{
    __typename
    ... on ProjectV2FieldCommon { id name }
    ... on ProjectV2SingleSelectField { options { id name } }
  }}
}}}'
```

Then pull every item with its Status, owner, labels and body:

```bash
gh api graphql -f query='
query { user(login:"OWNER") { projectV2(number: N) {
  items(first:100){ totalCount nodes{
    content{ __typename
      ... on Issue { number title body state
        assignees(first:5){ nodes{ login } }
        labels(first:10){ nodes{ name } } }
      ... on DraftIssue { title } }
    fieldValues(first:25){ nodes{
      ... on ProjectV2ItemFieldSingleSelectValue {
        name
        field{ ... on ProjectV2SingleSelectField{ name } } } }
  }}}
}}}'
```

Notes:
- User-owned boards use `user(login:...)`; org boards use `organization(login:...)`.
- The board number is the integer in the URL (`.../projects/2` → `number: 2`).
- A `DraftIssue` has no `number`/`assignees` — handle it separately.
- To **move** an item's status: `gh project item-edit --id <item-id>
  --field-id <status-field-id> --single-select-option-id <option-id>`.
  Get the option ids from the field-discovery query above.

## 5. Pitfalls learned this session

1. **`pkill -f 'gh auth login'` kills your own shell.** The pattern matches
   the command line of the shell that *contains* that string, so the
   launcher kills itself (exit -15 / SIGTERM). Kill by PID instead:
   `ps -eo pid,cmd | grep '[g]h auth'` then `kill <pid>`. The `[g]` trick
   stops grep matching its own line.
2. **Backgrounding a blocking login.** `cmd &` inside a single
   `run_command` call can still make the wrapper wait for the child. Use
   `setsid ... > log 2>&1 < /dev/null & disown` so the process detaches into
   its own session and the launcher returns.
3. **Device codes expire (~15 min).** If the user hasn't authorized in time,
   the poll never succeeds. Generate a fresh code rather than waiting longer.
4. **`gh` not on PATH after a user install.** Remember to `export
   PATH="$HOME/.local/bin:$PATH"` in every command, or use the absolute
   `~/.local/bin/gh`.
5. **`gh api graphql` rate limits.** Unauthenticated GraphQL is rejected
   outright; authenticated is 5000/hour. A 401 on GraphQL means the token
   is missing, not that the query is wrong.
6. **tar `--strip-components` mismatch.** The gh tarball is prefixed
   `gh_<ver>_linux_amd64/`, so the binary path is
   `gh_<ver>_linux_amd64/bin/gh`, not `./bin/gh`. List with
   `tar -tzf` first if extraction says "Not found in archive".

## 6. Verification before you report success

A GitHub task is not done until you confirm the side effect actually landed:

```bash
gh auth status                                   # who am I, what scopes
gh issue view <n> --repo owner/repo              # did the issue change?
gh pr view <n> --repo owner/repo --json state,mergedAt
git log --oneline -3 && git status -sb          # commit landed, tree clean
```

Paste the command output that proves the result. "I ran the command" and "the
change is on GitHub" are different claims — verify the second one. If a write
reported success but the read-back disagrees, trust the read-back and
investigate (usually a scope or a wrong repo/owner).
