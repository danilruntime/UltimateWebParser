# Installing UltimateWebParser

UltimateWebParser is a Docker service: one build gives you the bot, the worker,
the pinned Chromium and the VNC stack. Nothing else is installed on the host —
no Python, no Playwright, no Chromium.

**Русская версия:** [`install.ru.md`](install.ru.md).

If you only want to know whether this project is for you, read
[`../README.md`](../README.md) first. This page is the install path.

## What you need

- **Linux:** Docker Engine 24 or newer with the Compose v2 plugin. Check with
  `docker compose version`; the separate `docker-compose` v1 binary is not what
  the documented commands use.
- **Windows 10 or 11:** Docker Desktop with the **WSL 2 backend** enabled. Docker
  Desktop installs WSL 2 for you on current versions; the Ubuntu distribution is
  installed separately.
- **x86_64 (amd64).** The browser is an x86_64 Linux build; ARM hosts are not
  supported.
- About 4 GB of free RAM and 3 GB of disk for the image, the browser and one
  profile.
- Ports 6000–6100 free on the host's loopback interface. They are published on
  `127.0.0.1` only, so they do not need to be open in a firewall.
- A Telegram bot token from [@BotFather](https://t.me/BotFather) and your own
  Telegram user id.

A server without a desktop is fine: the VNC desktop exists inside the container,
and you open it in your own browser.

## Linux

### Prepare the files

```bash
git clone https://github.com/danilruntime/UltimateWebParser
cd UltimateWebParser
cp .env.example .env
```

Edit `.env`. Two entries are required:

```bash
UWP_BOT_TOKEN=123456789:AA...        # from @BotFather
UWP_ADMIN_IDS=111,222                # your Telegram id; comma-separated
```

A third is strongly recommended before you add any proxy or login:

```bash
UWP_SECRET_KEY=$(openssl rand -hex 32)
```

Without it, proxy and VNC passwords are stored in plaintext in `data/uwp.db`;
`uwp doctor` warns about that. Setting it *after* passwords exist makes those
passwords unreadable — there is no re-encryption path by design.

Everything else is optional and documented inline in `.env.example`. The full
reference is in the README's configuration table.

### Start the stack

Split mode is the recommended deployment: the bot polls Telegram, the worker
runs the browser.

```bash
docker compose -f compose.split.yaml -f compose.split.hostnet.yaml up -d --build
```

The second file puts the bot on the host network, which is only needed when
`UWP_TELEGRAM_PROXY` points at `127.0.0.1` on the host — a proxy running on the
same machine. Without such a proxy, drop it:

```bash
docker compose -f compose.split.yaml up -d --build
```

A single container is also possible and is what a bare `docker compose up`
starts:

```bash
docker compose up -d --build
```

Combined mode is simpler and uses one compose file, but restarting the bot
interrupts a run and the process that launches the browser also holds the bot
token. The README explains the trade-off.

The first build downloads Chromium (about 200 MB) and takes a few minutes.

### Check it runs

```bash
docker compose ps                                   # both services "healthy"
docker compose logs -f bot                          # the bot's own log
docker compose exec uwp-worker /entrypoint.sh doctor
```

`doctor` checks the data directories, the browser binary and its launch, the
VNC stack (a warning if Xvfb or x11vnc is missing), the token, the admins, the
encryption key and the migrations. It exits non-zero when something is broken.

In combined mode the container is called `uwp`:

```bash
docker compose exec uwp /entrypoint.sh doctor
```

Send `/start` to your bot. If it stays silent, the bot cannot reach
`api.telegram.org`: set `UWP_TELEGRAM_PROXY` and restart. The detailed proxy
section is in [`README.md`](README.md#the-telegram-proxy-in-detail).

## Windows (Docker Desktop and WSL 2)

Windows runs the same Linux containers. Docker Desktop provides the engine and
its WSL 2 backend, so the host kernel is Linux from the container's point of
view.

### Install the prerequisites

1. Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) and
   pick the **WSL 2 backend** in its settings. Virtualisation must be enabled in
   the BIOS/UEFI.
2. Install WSL 2 and an Ubuntu distribution from an elevated PowerShell:

```powershell
wsl --install -d Ubuntu
```

3. Open **Ubuntu** from the Start menu. That terminal is where every command
   below runs; Docker Desktop exposes the engine to it automatically, so
   `docker compose version` works there.
4. In Docker Desktop, enable **Settings → Resources → WSL integration** for that
   distribution.

### Get the code into WSL

Clone inside the WSL filesystem, not on the Windows drive:

```bash
cd ~
git clone https://github.com/danilruntime/UltimateWebParser
cd UltimateWebParser
cp .env.example .env
nano .env
```

`/mnt/c/...` works, but it is slow: every file operation crosses the 9p
filesystem, Chromium and SQLite do thousands of them, and file permissions are
emulated. A profile also carries live cookies, which the project protects with
POSIX modes — those are only real on the Linux side. Keep the repository and
`data/` under `~/`.

The `.env` entries are the same as on Linux: `UWP_BOT_TOKEN`, `UWP_ADMIN_IDS`,
and `UWP_SECRET_KEY` from `openssl rand -hex 32`.

### Start it

```bash
docker compose -f compose.split.yaml -f compose.split.hostnet.yaml up -d --build
```

There is no host-network caveat on Docker Desktop: a proxy listening on the
Windows side is reachable from a container as `host.docker.internal`, so
`UWP_TELEGRAM_PROXY=socks5://host.docker.internal:1080` works with the plain
`compose.split.yaml` too. Use the hostnet overlay only if you specifically need
the bot to share the WSL network namespace.

If Docker Desktop has just been updated, give the engine a moment before the
build starts; `docker info` should answer without an error.

### Reach the bot and noVNC from Windows

Windows forwards `localhost` to the Docker engine, so a VNC link the bot sends
(`http://localhost:6001/vnc.html`) opens in Edge or Chrome on Windows without
any extra setup. The same is true for `docker compose logs` and `doctor` from the
Ubuntu terminal.

If a link does not open, check that the session exists
(`docker compose exec uwp-worker /entrypoint.sh vnc list`) and that the port in
the link matches the published band 6000–6100. Windows may reserve part of that
band for Hyper-V; `netsh interface ipv4 show excludedportrange protocol=tcp`
lists the reservations, and the list shifts after a reboot — this is the one
Windows-specific failure mode that has no Linux equivalent.

`UWP_DATA_DIR` stays `data`: the bind mount `./data:/data:z` is resolved inside
WSL, not on `C:`.

## Why a native Windows run is not supported

The project does not ship a Windows build, and this is a decision rather than a
missing feature:

- **The browser is a Linux binary.** `fingerprint-chromium` is pinned by sha256
  as an x86_64 Linux ELF archive; `uwp browser install` downloads that archive
  and expects `chrome` inside it. Windows cannot execute it.
- **The VNC stack is Linux.** Xvfb, x11vnc, fluxbox and websockify are separate
  Linux programs; the login window is an X11 display. There is no Windows
  equivalent to swap in, and the kiosk relies on fluxbox grabbing key chords.
- **The sandbox is Docker's seccomp profile.** `docker/seccomp-uwp.json` allows
  Chromium to create its namespaces. Without it the browser refuses to start;
  the file is a Linux seccomp-bpf policy, not a Windows ACL.
- **The entrypoint is a Linux script.** It runs as root once, chowns the volume
  to `1000:1000`, drops privileges with `setpriv`, and relies on `chmod`,
  `umask` and `PR_SET_DUMPABLE` to keep the database, the WAL and the process
  environment away from the browser. None of that exists on Windows.
- **The code uses POSIX primitives.** `fcntl.flock` serialises migrations across
  the bot and the worker; file modes `0700`/`0600` protect cookies and
  passwords; neither has a faithful Windows counterpart.

WSL 2 is not a port of any of this: it is a Linux kernel running the Linux
container, with Docker Desktop in front of it. That is why it works and a native
run does not.

## First steps

1. Send `/start`. The bot creates the database and seeds one **disabled** site —
   «Steam — deals» on an `en` deployment, «Steam — скидки» on a `ru` one.
2. Open «🌐 Sites», press «🟢 Enable» on that card. The first run is queued at
   once, and the `.ods` report arrives in the chat.
3. Add a proxy under «🌍 Proxies» if the site needs a specific country, and
   attach it to the site in «⚙️ Settings».
4. For a site that needs a login, create a profile under «👤 Profiles», attach
   it to the site, and press «🖥 Log in to profile». The link and password
   arrive as a message; log in, then press «✅ Done».

Reports land in `data/exports/<site-slug>/` on the host, and the last 50 per site
are kept.

## Upgrading

```bash
git pull
docker compose -f compose.split.yaml -f compose.split.hostnet.yaml up -d --build
```

Migrations are append-only and applied automatically at start, so `data/` is
carried forward — the database, the profiles with their cookies and the
exports. A downgrade is not supported: an older image refuses a database that a
newer one has migrated.

Back up `data/` before a major upgrade. It is a directory of ordinary files, and
`docker compose down` does not remove it.

## Uninstalling

```bash
docker compose -f compose.split.yaml -f compose.split.hostnet.yaml down
rm -rf data
```

`down` removes the containers; `data/` holds the database, the browser profiles
with live cookies, the reports and the logs. Deleting it is irreversible, so
export anything you want to keep first.

The image stays cached until `docker image rm` — the compose files build it under
the name `uwp`.

## Where to read next

| Document | What it answers |
|---|---|
| [`../README.md`](../README.md) | what the project does, how it is built, the configuration table |
| [`README.md`](README.md) | the operating guide: permissions, the Telegram proxy, VNC, reports, the CLI, writing a parser |
| [`../SECURITY.md`](../SECURITY.md) | the threat model and the known limitations |
| [`../DISCLAIMER.md`](../DISCLAIMER.md) | lawful use, terms of service and the operator's responsibility |
| [`../docker/README-seccomp.md`](../docker/README-seccomp.md) | the three seccomp relaxations and how to rebuild the profile |
