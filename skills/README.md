# Skills

Short, task-focused notes that AI coding assistants load on demand — the things you'd otherwise
re-explain every session, or rediscover the hard way. They're plain Markdown, so they're also
perfectly readable on their own if you just want the cheat-sheet.

## Install

```bash
./skills/install.sh
```

That symlinks every skill into the two places the tools look:

| Tool | Location | Points at |
|---|---|---|
| Claude Code | `.claude/skills/<name>/` | the skill directory |
| Codex CLI | `.agents/skills/<name>.md` | the `SKILL.md` inside it |

Both directories are gitignored, which is the reason this script exists: the skills themselves are
committed, but the links are per-checkout and have to be recreated on every machine.

Skills can also ship a **command**. Any executable in `skills/<name>/bin/` is linked onto your
`PATH` — into `$SKILLS_BIN` if set, otherwise `~/.local/bin` *when that directory already exists*.
It is never created for you, so this cannot invent a PATH directory on a machine that does not use
that convention; if it is skipped or is not on your `PATH`, the script says so. `wled-scan` ships
`wleds` this way.

Re-run it any time. It rewrites links rather than skipping ones that already exist, so a link that
is broken or points at a renamed skill gets repaired instead of being quietly reported as fine. It
also prunes dangling links left behind by deleted or renamed skills. It will not touch anything in
those directories that isn't a symlink into `skills/`.

```bash
./skills/install.sh --dry-run    # show what would change
```

**After pulling changes that add or rename a skill, run it again** — new skills won't load until
they're linked.

## What's here

| Skill | For |
|---|---|
| `wled-scan` | Find every WLED device on the LAN and Tailscale and table their config. Provides the `wleds` command. |
| `flash` | Build firmware and push it to a device over HTTP OTA, then prove the running build is the one you meant. |
| `check-device` | Ask a device what it's running — version, commit, branch, uptime, heap. |
| `configure-device` | Set a device up over the JSON API: LED bus type and pins, 2D matrix, I2C, ESP-NOW. Also the traps where a config looks accepted but isn't. |
| `new-effect` | Adding an LED effect to `usermods/ampworks` — skeleton, the PROGMEM descriptor format, registration, audio and touch data. |
| `amp-coding-style` | House style for C/C++ and Python in this repo. |

Rough order for a new device: `wleds` (find it) → `flash` → `check-device` → `configure-device`.

## No addresses, no absolute paths

Skills take the target device as an argument or via `WLED_IP`, and fall back to mDNS
(`wled.local`). None of them hardcode an IP.

That's deliberate rather than tidiness. These devices move between networks and get re-addressed
by DHCP, so a baked-in address eventually points at *something else* — and pushing a config or a
firmware image to the wrong device is not a friendly failure. Same reasoning for paths: everything
is relative to the repo, so a checkout in a different location on a different machine just works.

```bash
export WLED_IP=<host-or-ip>          # or pass as the first argument
dns-sd -B _wled._tcp                 # macOS — discover devices
avahi-browse -rt _wled._tcp          # Linux
```

## Writing a new one

Create `skills/<name>/SKILL.md` with YAML frontmatter, then re-run `install.sh`:

```markdown
---
name: my-skill
description: One sentence covering what it does and when to reach for it. This is the only
  part the assistant sees before deciding whether to load the skill, so make it specific.
---

Body: commands, tables, gotchas.
```

Optional frontmatter:

- `disable-model-invocation: true` — only runs when a human explicitly asks for it. Use for
  anything that writes to hardware or is otherwise not free to try.
- `user-invocable: false` — reference material the assistant may consult, not a command.

What makes these worth keeping:

- **Write down the trap, not just the happy path.** Most of the value here is in the failures —
  a config that returns `{"success":true}` and does nothing, a re-flash that can't fix a persisted
  setting, a stale build cache reporting a member that plainly exists. The commands are easy to
  look up; those aren't.
- **Say why, briefly.** A rule with no reason gets "cleaned up" by the next person.
- **Keep it current.** A skill that's confidently wrong is worse than none, because it will be
  trusted. If you change behaviour it describes, change the skill in the same commit.
- **Don't duplicate.** Cross-reference the neighbouring skill instead of restating it.
