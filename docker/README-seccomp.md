# Chromium sandbox inside the container

`seccomp-uwp.json` is Docker's stock seccomp profile
(`moby/moby v28.0.0`, `profiles/seccomp/default.json`) with **three** rules
changed so Chromium can build its own sandbox.

That file is licensed Apache-2.0 by Docker, Inc. and is a modified work; the
attribution and the full license text are in
[`../THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md).

## Why the stock profile is not enough

Chromium builds its sandbox from namespaces: the zygote puts the renderer into
its own mount namespace, then drops privileges there. Three stock rules refuse
that, and Chromium aborts with:

```
FATAL:content/browser/zygote_host/zygote_host_impl_linux.cc:128
  No usable sandbox!
```

| Rule | Stock | Problem |
|---|---|---|
| `clone` | allowed only when **all** namespace flags are set at once (`0x7E020000`) | Chromium asks for `CLONE_NEWNS` (`0x20000`) alone |
| `clone3` | `SCMP_ACT_ERRNO` (ENOSYS) without `CAP_SYS_ADMIN` | modern glibc reaches the same request through `clone3` |
| `unshare` | allowed only with `CAP_SYS_ADMIN` | Chromium finishes the mount namespace with `unshare(CLONE_NEWNS)` |

All three were measured, not guessed: dropping any one of them brings the
`No usable sandbox` abort back.

That is why the image used to set `UWP_NO_SANDBOX=true`, and why the renderer
ran unconfined — it shared the container's user and PID namespaces, so a
compromised page could read `/data` (cookies, database) and the bot token from
`/proc/<pid>/environ`.

## What the patched profile buys

Measured in the built image, uid 1000, via the application's own
`uwp.browser.launch()` path:

| | `--no-sandbox` (before) | this profile (after) |
|---|---|---|
| renderer user namespace | container's own | **its own** |
| renderer mount namespace | container's own | **its own** |
| `NoNewPrivs` | 0 | **1** |
| `/data` from the renderer | readable | **not visible** |
| app `/proc/<pid>/environ` | readable | **not readable** |
| `unshare(CLONE_NEWUSER)` as uid 1000 | `EPERM` | **`EPERM`** (still) |
| `mount` / `setns` as uid 1000 | `EPERM` | **`EPERM`** (still) |

The new rules are deliberately narrow: `clone` matches `CLONE_NEWNS` only,
`clone3` stays restricted to callers without `CAP_SYS_ADMIN`, and the added
`unshare` rule matches `CLONE_NEWNS` only — so `unshare(CLONE_NEWUSER)` remains
forbidden. Nothing else in the profile changed.

## Keeping it current

Regenerate rather than hand-editing:

```bash
curl -fsSL -o /tmp/default.json \
  https://raw.githubusercontent.com/moby/moby/v28.0.0/profiles/seccomp/default.json
# then re-apply the three edits and diff against this file
```

The edits are the only `comment` fields in the JSON besides the stock
`s390 parameter ordering` one.

## Requirements

The sandbox needs uid ≠ 0. The container already drops to uid 1000 through
`setpriv` in `entrypoint.sh`, so this holds. Running the image as root without
the profile makes Chromium refuse to start — which is the honest failure.
Operators who cannot use the profile still have the escape hatch:
`UWP_NO_SANDBOX=true`.
