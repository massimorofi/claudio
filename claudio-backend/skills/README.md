# Claudio skills

This is the **skills directory**: `claudio-backend/skills/`.

It is the directory the backend scans for installed Agent Skills. The path
comes from `agent.skills_dir` in `claudio-backend/config/config.yaml`
(default `./skills`), anchored to this `claudio-backend/` folder regardless
of where the backend is launched from.

The original pre-refactor skills directory was lost and is not recoverable
from this repo's git history. Recreate skills here.

## Adding a skill

Each skill is **one folder** with a `SKILL.md`:

```
skills/
└── my-skill/
    ├── SKILL.md          # required
    └── ...               # optional bundled scripts / reference files
```

`SKILL.md` is YAML frontmatter followed by markdown instructions:

```markdown
---
name: my-skill
description: One sentence on when this skill is relevant.
---

Full instructions here. Reference bundled files by paths relative to this
skill's own folder.
```

Rules:
- `name` is lowercase-hyphenated and **must equal the folder name**.
- `description` is required, ≤ 1024 characters.
- Allowed frontmatter keys only: `name`, `description`, `license`,
  `compatibility`, `metadata`, `allowed-tools`.

Only direct child folders are scanned. A folder that fails validation is
reported as broken (via `GET /api/v1/skills`) rather than dropped.

For the full picture — including the workspace location and the path
resolution rules — see [doc/skills-and-workspace.md](../../doc/skills-and-workspace.md).
