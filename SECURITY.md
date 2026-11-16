# Security Policy

UltimateWebParser runs other people's code (the pages its browser parses) next
to other people's secrets (proxy passwords, account cookies, the bot token).
The threat model is therefore written down rather than assumed.

This document covers both what is **protected** and what is deliberately
**not** protected. The second list matters more: it lets you decide whether this
service suits you before you hand it an account.

**Русская версия:** [`SECURITY.ru.md`](SECURITY.ru.md).

## Threat model

**Who attacks, and through what:**

1. **The page the browser parses.** Hostile HTML/JS on the target site,
   exploiting the Chromium renderer or reading the process environment.
2. **A process with the same uid inside the container.** The sharpest case is
   the VNC browser used for manual login: it runs under the same `uid 1000` as
   the worker and executes arbitrary content from any site you visit.
3. **A site asked to open through the proxy.** An attempt to turn a paid proxy
   into a path to `127.0.0.1`, RFC 1918 space, or cloud metadata.
4. **Someone who obtained `.env` or the `data/` directory.** Here the defence is
   encryption at rest and file permissions, not network isolation.

**In scope:** renderer isolation, reading `/proc/*/environ` from processes with
the same uid, SSRF through the proxy bridge, secrets leaking into logs and bot
messages, link forgery in `.ods`, escaping `data/exports` through a filename,
and an outsider reaching the bot.

**Out of scope:**
- an attacker with root on the host;
- an attacker who has read `.env` (`UWP_SECRET_KEY` and the bot token);
- a compromise of `fingerprint-chromium` itself (the binary is pinned by
  sha256 but not audited);
- targeted attacks on a specific installation (social engineering, physical
  access).

## What is protected, and by what

| Threat | Defence | Where |
|---|---|---|
| Renderer escape to `/data` and `/proc` | Chromium sandbox on; Docker seccomp profile with three narrow relaxations | `browser.py` (`chromium_sandbox=True`), `docker/seccomp-uwp.json` |
| Reading `UWP_BOT_TOKEN`/`UWP_SECRET_KEY` through `/proc/<pid>/environ` | `PR_SET_DUMPABLE=0` before the database is opened; the VNC browser shares the uid | `env.hide_process_environment()`, `cli.py` |
| Environment leaking into browser processes | An allowlist instead of inherited env | `env.browser_env()` |
| SSRF through the proxy bridge | Refusal for loopback, RFC 1918, link-local (including `169.254.169.254`), `*.internal`, `*.local`, multicast | `proxybridge._is_internal_host` |
| Proxy and VNC passwords in the database | Fernet + PBKDF2-HMAC-SHA256 (480k iterations) when `UWP_SECRET_KEY` is set | `secretbox.py` |
| Proxy passwords in logs and chat | The bridge logs `scheme://host:port` without the password; the proxy card in the bot shows only the login; `doctor` masks the Telegram proxy as `user:***@host` | `proxybridge._describe`, `bot/texts.proxy_card`, `cli._mask_proxy` |
| Other host users reaching the database and profiles | `data/` = 0700, database and WAL/SHM = 0600, `umask 077` | `config.py`, `database.py`, `cli.py` |
| An outsider in the bot | Middleware on `dp.update`, i.e. on **every** update type | `bot/middleware.py` |
| The bot token in the process that launches the browser | In split mode `UWP_BOT_TOKEN` is empty for the worker | `compose.split.yaml` |
| VNC exposed to the network | Ports 6000–6100 are published on `127.0.0.1` only; RFB listens with `-localhost` | `compose*.yaml`, `vnc.py` |
| `javascript:`/`data:`/`file:` in a report | Only `http(s)` becomes clickable | `ods.safe_href` |
| Escaping `data/exports` through a report name | Slugify of substitutions plus `/` and `\` stripped | `ods.report_filename` |
| SQL injection | Values only through placeholders (`?`) | `db/repo/*` |

## Known limitations

These are not a bug list but decisions with a known price. Each is a deliberate
choice, not an oversight.

**1. The VNC password is 8 characters.** The RFB protocol checks only the first
eight bytes; a longer string would display a character the server ignores. The
password lives in the URL fragment (`#password=`), so it does not reach access
logs or `Referer`, but it is visible in browser history.

**2. noVNC speaks HTTP.** It is not exposed to the network — loopback only.
Remote access means an SSH tunnel or a TLS reverse proxy
(`UWP_PUBLIC_SCHEME=https`).

**3. `UWP_NO_SANDBOX=true` removes renderer isolation.** An emergency mode for
hosts where the seccomp profile cannot be applied. The renderer then shares the
container's namespaces and can see `/data`. Do not make this the default.

**4. Without `UWP_SECRET_KEY` proxy and VNC passwords are stored in the clear.**
`uwp doctor` warns about it. Set the key **before** adding a proxy: changing the
key makes the old values unreadable — by design, not by oversight.

**5. The proxy bridge does not authenticate its client.** Chromium cannot send
`Proxy-Authorization` into a `CONNECT` tunnel, so a local bridge adds the
password. Any process in the container can use that bridge. The limitation is
offset by refusing internal addresses, not by authentication.

**6. In combined mode the bot token lives in the same process as the browser.**
Hence `PR_SET_DUMPABLE=0` and the env allowlist. If that is unacceptable, use
split mode.

**7. There is no rate limiting on bot actions.** The allowlist gate does not
limit frequency: an admin holding a button down will generate load only for
themselves. That is acceptable for a single administrator; it is not for
several.

**8. Profiles hold live cookies and are not encrypted.** Only file permissions
(`0700`) and the renderer sandbox protect them. The browser itself reads that
directory by necessity — it *is* `--user-data-dir`. Anyone with access to
`data/profiles` on the host therefore obtains the account sessions;
`UWP_SECRET_KEY` does not help here, it encrypts only proxy and VNC passwords in
the database.

**9. Multiple installations on one machine are unsupported.** Ports 6000–6100
are global; a second stack breaks the first one's VNC. That is an operational
limitation, but it has a security consequence: someone working around it may
weaken the isolation.

## Reporting a vulnerability

**Do not open a public issue.** Use one of these channels:

- GitHub → **Security** → **Report a vulnerability** (a private advisory), or
- email the maintainer listed on the GitHub profile.

A useful report states: the version (from `pyproject.toml` or the «Метаданные»
sheet of the latest report), the mode (`combined` or `split`), what exactly is
reachable and under which conditions, and a minimal reproduction step. If the
vulnerability needs code already executed in the renderer, or access to the
host, say so plainly — it changes the assessment.

**What to expect:** an acknowledgement within 7 days. If the vulnerability is
confirmed, the fix and a test that reproduces it go in one commit describing the
measured symptom — as the project history already does
(`git log --grep="fix(security)"`).

## What this project does not promise

This is a self-hosted tool for a single administrator, not a multi-tenant
service with tenant isolation. It does not claim protection from the host
administrator, from malicious code on target sites exploiting an unknown
Chromium vulnerability, or from a targeted attack on a specific installation.
Use it where you trust the host and the sites you parse.
