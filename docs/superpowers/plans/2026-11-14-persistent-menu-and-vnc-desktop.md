# Persistent Menu, Full-Size VNC Desktop, and Deferred Fixes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the bot a permanent reply keyboard, make the VNC desktop look like a 1920×1080 Windows 10 screen with a taskbar and a browser filling the work area, and close seven deferred defects from the branch review.

**Architecture:** Four independent slices. (1) A `ReplyKeyboardMarkup` in `keyboards.py` plus text-matched handlers that clear the FSM state, so a menu tap during a wizard abandons the wizard instead of feeding it the button's text. (2) A generated fluxbox `init`/`apps` pair written per VNC session, a 38 px toolbar, and a 1920×1040 window size for the headful login browser only. (3) A one-shot DB sweep of orphaned VNC rows at worker startup. (4) Four small robustness fixes in `proxybridge.py`, `worker.py` and the Steam parser.

**Tech Stack:** Python 3.12 in the image, aiogram 3.31, Playwright, fluxbox 1.3.5 / Xvfb, pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-11-14-persistent-menu-and-vnc-desktop-design.md`

## Global Constraints

- Bot UI copy is Russian; code, identifiers, logs and commits are English. Conventional commits (`fix:`, `feat:`, `test:`).
- Every fix is TDD: write the failing test, run it and see it fail, implement, run it and see it pass, commit.
- The image runs Python 3.12; the local venv is 3.14. Any module whose method or attribute name shadows a builtin (`list`, `get`, `due`) needs `from __future__ import annotations`.
- Tests: `.venv/bin/python -m pytest -p no:cacheprovider`. Lint: `.venv/bin/python -m ruff check src tests`.
- `_pipe` in `proxybridge.py` must never gain a timeout: an idle tunnel is legitimate while a page is open.
- VNC geometry is fixed by measurement, not by taste: toolbar **38** px, window **1920×1040** at **0,0**, Xvfb screen **1920×1080**.
- **Do not add `--start-maximized`.** Measured in the image: with `--fingerprint` present the window lands at 945×1020 regardless of `--start-maximized`. Only `--window-size` controls the geometry.
- Full suite before any commit: `384 passed, 3 skipped` is the current baseline; it must not regress.

## Measured Facts (do not re-derive)

All values measured in a throwaway container from `ultimate-web-parser:latest`, Xvfb `1920x1080x24`, fluxbox 1.3.5:

| fluxbox toolbar height | `_NET_WORKAREA` | Chromium outer size |
|---|---|---|
| unset (style default 18) | 1920×1060 | — |
| 40 | 1920×1038 | 1920×1038 |
| **38** | **1920×1040** | **1920×1040 at 0,0** |

- fluxbox adds 2 px of border to the toolbar, so 38 px renders a 40 px band — exactly Windows 10's taskbar at 1080p.
- Fluxbox reads `$HOME/.fluxbox/init` and `$HOME/.fluxbox/apps`. `-rc` picks up `appsFile` but silently ignores `toolbar.height`.
- `[app] (class=Chromium) { [Deco] {NONE} }` removes the frame; without it a window loses another ~20 px to a title bar.
- With `--window-size=1920,1040 --window-position=0,0` plus `Deco {NONE}`: `outerWidth/outerHeight` = 1920×1040, `screen.availHeight` = 1040, `innerWidth` = 1920, `innerHeight` = 863 (measured in the rebuilt image; an earlier run with a slightly different flag set gave 1912×859).
- **`Deco {NONE}` alone is not enough.** The `apps` rule only applies to windows fluxbox sees after it reads the file, and the browser is spawned right after the WM, so the rule loses the race: `_NET_FRAME_EXTENTS` measured `1,1,20,4` with the window at `-1,15` (a 20 px title bar pushed off-screen). `session.screen0.defaultDeco: NONE` in `init` is read before any mapping and gives `0,0,0,0`.
- `--start-maximized` alongside `--fingerprint=<seed>` yields 945×1020 at 0,0 (reproduced three times).
- fluxbox logs `fbsetbg: xmessage: not found` and many `Failed to read: ... Setting default value` lines. Harmless; they go to the per-session log file.

## Review Focus

Input classes the spec implies but no single task's tests fully exercise:

1. **A reply-keyboard tap arriving mid-wizard.** An admin taps «🌐 Сайты» while the bot awaits a profile name. The wizard must be abandoned, not answered with the button text. Owned by Task 2.
2. **`toolbar.height` written where fluxbox does not read it.** A config in the wrong path yields the wrong work area with no error. Owned by Task 3, verified in-image in Task 3 Step 5.
3. **An upstream proxy that accepts TCP and then says nothing.** Must become a `ProxyError` in 15 s, not a 300 s failure that blames the site. Owned by Task 5.
4. **A VNC row left `running` by a previous process lifetime.** It must be `failed` before the first tick, or it blocks its port and its profile for the whole TTL. Owned by Task 4.
5. **A `LimitOverrunError` from `readuntil`.** It currently reaches `_handle` as an unhelpful message and the `len(head) > _HEAD_LIMIT` branch is unreachable. Owned by Task 6.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/uwp/bot/keyboards.py` | Add `reply_menu_kb()` and four `BTN_*` constants. Existing inline keyboards unchanged. |
| `src/uwp/bot/handlers/common.py` | Four text handlers that clear FSM state; `/start` attaches the reply keyboard. |
| `src/uwp/vnc.py` | Desktop constants, `_write_fluxbox_home()`, per-session WM home, window-size flags. |
| `src/uwp/profiles.py` | `browser_flags` gains an optional `window_size` override (headful only). |
| `src/uwp/db/repo/vnc.py` | `sweep_orphans() -> list[int]`. |
| `src/uwp/app.py` | Call the sweep once in `run_worker`, free profiles via `release_login`. |
| `src/uwp/proxybridge.py` | Timeouts on three handshakes, first-completed tunnel teardown, actionable head-limit error, single `parse_proxy_url`. |
| `src/uwp/worker.py` | `gather(return_exceptions=True)` in `run_forever` — a crashed slot must not cancel a live sibling. |
| `src/uwp/parsers/steam_deals/parser.py` | Signed numbers in `_NUMBER_RE`. |

---

### Task 1: Reply keyboard widget

**Files:**
- Modify: `src/uwp/bot/keyboards.py`
- Test: `tests/unit/test_profile_service.py` (keyboard tests already live there)

**Interfaces:**
- Produces: `BTN_SITES = "🌐 Сайты"`, `BTN_PROFILES = "👤 Профили"`, `BTN_PROXIES = "🌍 Прокси"`, `BTN_HELP = "❓ Помощь"`; `reply_menu_kb() -> ReplyKeyboardMarkup`.

- [ ] **Step 1: Write the failing test**

```python
def test_reply_menu_kb_is_a_two_by_two_keyboard():
    from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

    from uwp.bot import keyboards

    kb = keyboards.reply_menu_kb()

    assert isinstance(kb, ReplyKeyboardMarkup)
    texts = [b.text for row in kb.keyboard for b in row]
    assert texts == [
        keyboards.BTN_SITES,
        keyboards.BTN_PROFILES,
        keyboards.BTN_PROXIES,
        keyboards.BTN_HELP,
    ]
    assert all(isinstance(b, KeyboardButton) for row in kb.keyboard for b in row)
    assert kb.resize_keyboard is True
```

- [ ] **Step 2: Run it and see it fail**

Run: `.venv/bin/python -m pytest tests/unit/test_profile_service.py -q -k reply_menu`
Expected: FAIL — `module 'uwp.bot.keyboards' has no attribute 'reply_menu_kb'`.

- [ ] **Step 3: Implement**

Extend the import to `InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup`, then append:

```python
BTN_SITES = "🌐 Сайты"
BTN_PROFILES = "👤 Профили"
BTN_PROXIES = "🌍 Прокси"
BTN_HELP = "❓ Помощь"


def reply_menu_kb() -> ReplyKeyboardMarkup:
    """The permanent keyboard under the chat input.

    ``resize_keyboard`` keeps it two rows tall on a phone instead of letting
    Telegram stretch each button to full width.
    """
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_SITES), KeyboardButton(text=BTN_PROFILES)],
            [KeyboardButton(text=BTN_PROXIES), KeyboardButton(text=BTN_HELP)],
        ],
        resize_keyboard=True,
    )
```

- [ ] **Step 4: Run it and see it pass**

Run: `.venv/bin/python -m pytest tests/unit/test_profile_service.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/uwp/bot/keyboards.py tests/unit/test_profile_service.py
git commit -m "feat: add a permanent reply keyboard to the bot"
```

---

### Task 2: Menu handlers that abandon a wizard instead of feeding it

**Files:**
- Modify: `src/uwp/bot/handlers/common.py`
- Test: `tests/unit/test_bot_menu.py` (create)

**Interfaces:**
- Consumes: `keyboards.reply_menu_kb()`, `keyboards.BTN_*` (Task 1).
- Produces: four `Message` handlers named `menu_*` in the `common` router.

**Why no `StateFilter(None)`:** `common` is registered before `profiles` in `app.py`, so a filter that rejects FSM states would let a mid-wizard tap fall through to the wizard's own text handler, which would then create a profile named «🌐 Сайты». The handler must always match and clear the state itself.

- [ ] **Step 1: Write the failing test**

```python
"""The permanent keyboard must match mid-wizard and abandon the wizard."""

import pytest

from uwp.bot.handlers import common


class _Service:
    async def list(self):
        return []


class _Deps:
    def __init__(self):
        self.sites = _Service()
        self.profiles = _Service()
        self.proxies = _Service()
        self.runs = _Service()


def _menu_handlers(router):
    return [
        observer
        for observer in router.message.handlers
        if observer.callback.__name__.startswith("menu_")
    ]


def test_all_four_reply_buttons_have_handlers():
    router = common.build_router(settings=None, deps=_Deps())

    names = {observer.callback.__name__ for observer in _menu_handlers(router)}

    assert names == {"menu_sites", "menu_profiles", "menu_proxies", "menu_help"}


def test_the_handlers_are_not_restricted_to_an_empty_state():
    """A StateFilter here would let a mid-wizard tap reach the wizard handler."""
    from aiogram.filters import StateFilter

    router = common.build_router(settings=None, deps=_Deps())

    for observer in _menu_handlers(router):
        assert not any(
            isinstance(flt.callback, StateFilter) for flt in observer.filters
        ), observer.callback.__name__
```

- [ ] **Step 2: Run it and see it fail**

Run: `.venv/bin/python -m pytest tests/unit/test_bot_menu.py -q`
Expected: FAIL — no `menu_*` handlers exist.

- [ ] **Step 3: Implement**

`build_router` already resolves `deps.sites` and `deps.runs`; add `profiles_service = deps.profiles` and `proxies_service = deps.proxies`. Import `FSMContext` and `profiles` handlers module. Change `/start`, then add four handlers before `return router`:

```python
@router.message(Command("start"))
async def start_message(message: Message) -> None:
    await message.answer(texts.MENU, reply_markup=keyboards.reply_menu_kb())


# These match regardless of FSM state on purpose. A tap during a wizard must
# abandon the wizard: with a StateFilter(None) it would fall through to the
# wizard's text handler and create a profile named «🌐 Сайты».
@router.message(F.text == keyboards.BTN_SITES)
async def menu_sites(message: Message, state: FSMContext) -> None:
    await state.clear()
    await send_sites(message, sites_service, settings)


@router.message(F.text == keyboards.BTN_PROFILES)
async def menu_profiles(message: Message, state: FSMContext) -> None:
    await state.clear()
    items = await profiles_service.list()
    text = texts.PROFILES_HEADER if items else texts.NO_PROFILES
    await message.answer(text, reply_markup=profiles.profiles_kb(items))


@router.message(F.text == keyboards.BTN_PROXIES)
async def menu_proxies(message: Message, state: FSMContext) -> None:
    await state.clear()
    items = await proxies_service.list()
    text = texts.PROXIES_HEADER if items else texts.NO_PROXIES
    await message.answer(text, reply_markup=profiles.proxies_kb(items))


@router.message(F.text == keyboards.BTN_HELP)
async def menu_help(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(texts.HELP)
```

`send_sites` already exists in the same module and sends a new message. Because these live in `common`, they run before `profiles`' FSM handlers and before `build_fallback_router`.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/unit/test_bot_menu.py tests/unit/test_bot_access.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/uwp/bot/handlers/common.py tests/unit/test_bot_menu.py
git commit -m "feat: open bot sections from the permanent keyboard"
```

---

### Task 3: Fluxbox home with a Windows-sized taskbar

**Files:**
- Modify: `src/uwp/vnc.py` (constants, `_write_fluxbox_home`, `_Stack`, factory spawn, headful window flags)
- Modify: `src/uwp/profiles.py` (`browser_flags` window-size override)
- Test: `tests/unit/test_vnc.py`, `tests/unit/test_profiles.py`

**Interfaces:**
- Produces: `DESKTOP_SIZE = (1920, 1080)`, `TASKBAR_HEIGHT = 38`, `WORKAREA = (1920, 1040)`; `_write_fluxbox_home(home: Path, toolbar_height: int) -> Path`.
- `browser_flags(..., window_size: tuple[int, int] | None = None)` appends `--window-size`/`--window-position` only when `headless=False` and `window_size` is given. Default `None` keeps every existing call unchanged.
- `_Stack.__init__` gains `wm_home: Path | None = None`, removed on `stop()`.

- [ ] **Step 1: Write the failing tests**

```python
def test_desktop_geometry_matches_windows_10():
    from uwp.vnc import DESKTOP_SIZE, TASKBAR_HEIGHT, WORKAREA

    assert DESKTOP_SIZE == (1920, 1080)
    # fluxbox adds 2 px of border, so 38 px renders a 40 px taskbar.
    assert TASKBAR_HEIGHT == 38
    assert WORKAREA == (1920, 1080 - 40)


def test_fluxbox_home_sets_the_toolbar_and_strips_decorations(tmp_path):
    from uwp.vnc import _write_fluxbox_home

    home = _write_fluxbox_home(tmp_path / "wm", toolbar_height=38)

    init = (home / ".fluxbox" / "init").read_text()
    assert "session.screen0.toolbar.height: 38" in init
    assert "session.screen0.toolbar.visible: true" in init
    assert "session.screen0.toolbar.placement: BottomCenter" in init
    apps = (home / ".fluxbox" / "apps").read_text()
    assert "[Deco] {NONE}" in apps
    assert "class=Chromium" in apps
```

In `tests/unit/test_profiles.py`:

```python
def test_browser_flags_size_a_headful_window(tmp_path):
    ident = identity_from_seed(7)

    flags = browser_flags(
        ident, tmp_path, None, headless=False, window_size=(1920, 1040)
    )

    assert "--window-size=1920,1040" in flags
    assert "--window-position=0,0" in flags
    # Measured: --start-maximized breaks the size when --fingerprint is set.
    assert "--start-maximized" not in flags


def test_browser_flags_ignore_window_size_when_headless(tmp_path):
    ident = identity_from_seed(7)

    flags = browser_flags(
        ident, tmp_path, None, headless=True, window_size=(1920, 1040)
    )

    assert not any(f.startswith("--window-size") for f in flags)
```

- [ ] **Step 2: Run them and see them fail**

Run: `.venv/bin/python -m pytest tests/unit/test_vnc.py -q -k "desktop_geometry or fluxbox_home"`
Expected: FAIL — `ImportError: cannot import name 'DESKTOP_SIZE'`.

- [ ] **Step 3: Implement**

```python
# The VNC desktop mimics Windows 10 at 1080p: a 40 px taskbar leaves a
# 1920x1040 work area. fluxbox adds 2 px of border, hence 38.
DESKTOP_SIZE = (1920, 1080)
TASKBAR_HEIGHT = 38
WORKAREA = (DESKTOP_SIZE[0], DESKTOP_SIZE[1] - TASKBAR_HEIGHT - 2)

_FLUXBOX_INIT = """\
session.styleFile: /usr/share/fluxbox/styles/Squared_for_Debian
session.appsFile: {home}/.fluxbox/apps
session.screen0.toolbar.height: {toolbar}
session.screen0.toolbar.widthPercent: 100
session.screen0.toolbar.placement: BottomCenter
session.screen0.toolbar.visible: true
session.screen0.toolbar.tools: workspacename, clock, iconbar, systemtray
session.screen0.strftimeFormat: %H:%M
"""

# Chromium draws its own tab strip, so a fluxbox frame is dead pixels. Without
# this, a 1040-high window loses another ~20 px to a title bar.
_FLUXBOX_APPS = """\
[app] (class=Chromium)
  [Deco] {NONE}
[end]
"""


def _write_fluxbox_home(home: Path, toolbar_height: int) -> Path:
    """Write the WM config fluxbox reads from ``$HOME/.fluxbox``.

    fluxbox honours ``$HOME/.fluxbox/init``; ``-rc`` picks up ``appsFile`` but
    silently ignores ``toolbar.height``, so the files must live here.
    """
    config = home / ".fluxbox"
    config.mkdir(parents=True, exist_ok=True)
    (config / "apps").write_text(_FLUXBOX_APPS)
    (config / "init").write_text(
        _FLUXBOX_INIT.format(home=home, toolbar=toolbar_height)
    )
    return home
```

In `_default_process_factory`, after `password_file` is known:

```python
wm_home = _write_fluxbox_home(
    password_file.parent / f"wm-{profile.id or 'x'}-{port}",
    TASKBAR_HEIGHT,
)
```

Change the Xvfb command to use the constant: `f"{DESKTOP_SIZE[0]}x{DESKTOP_SIZE[1]}x24"`.

Pass the window size into `browser_flags(...)` in that same call site:

```python
browser_flags(
    _identity(profile),
    Path(profile.user_data_dir),
    proxy_url,
    headless=False,
    no_sandbox=settings.no_sandbox,
    window_size=WORKAREA,
)
```

In `profiles.py`, add the parameter and the flags after the `--disable-features=Translate` block:

```python
if not headless and window_size is not None:
    flags += [
        f"--window-size={window_size[0]},{window_size[1]}",
        "--window-position=0,0",
    ]
```

Spawn fluxbox with its own `HOME`. The current loop is:

```python
for command in commands[1:]:
    env = browser_env if command[0] == executable else None
    processes.append(await _spawn(command, stderr=log_file, env=env))
```

Replace with a per-command env: `HOME=wm_home` for `fluxbox`, `browser_env` for the browser, `None` otherwise. Do **not** change the browser's `HOME` — Chromium needs the real one for its profile paths.

Pass `wm_home` into `_Stack(...)` and delete it in `stop()` with `shutil.rmtree(self.wm_home, ignore_errors=True)` after the processes are down, so per-session configs do not pile up under `data/vnc`.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/unit/test_vnc.py tests/unit/test_profiles.py -q`
Expected: PASS.

- [ ] **Step 5: Verify the geometry in the real image**

This catches a silently-ignored config key. Run inside a throwaway container from the current image:

```bash
docker run --rm --entrypoint sh ultimate-web-parser:latest -c '
apt-get update -qq >/dev/null 2>&1 && apt-get install -y -qq x11-utils wmctrl >/dev/null 2>&1
mkdir -p /tmp/w/.fluxbox
printf "session.screen0.toolbar.height: 38\nsession.screen0.toolbar.widthPercent: 100\nsession.screen0.toolbar.placement: BottomCenter\nsession.screen0.toolbar.visible: true\n" > /tmp/w/.fluxbox/init
printf "[app] (class=Chromium)\n  [Deco] {NONE}\n[end]\n" > /tmp/w/.fluxbox/apps
Xvfb :88 -screen 0 1920x1080x24 & sleep 2
HOME=/tmp/w DISPLAY=:88 fluxbox & sleep 3
DISPLAY=:88 /opt/uwp/browser/chrome --no-sandbox --fingerprint=12345 \
  --window-size=1920,1040 --window-position=0,0 --user-data-dir=/tmp/c about:blank & sleep 10
DISPLAY=:88 xprop -root _NET_WORKAREA
DISPLAY=:88 wmctrl -lG | grep -i chrom'
```

Expected: work area `1920, 1040`, window `1920 1040` at `0, 0`. A work-area height of 1060 means the toolbar config was not read.

- [ ] **Step 6: Commit**

```bash
git add src/uwp/vnc.py src/uwp/profiles.py tests/unit/test_vnc.py tests/unit/test_profiles.py
git commit -m "feat: give the VNC desktop a Windows-sized taskbar"
```

---

### Task 4: Sweep orphaned VNC rows at worker startup

**Files:**
- Modify: `src/uwp/db/repo/vnc.py`
- Modify: `src/uwp/app.py` (`run_worker`)
- Test: `tests/unit/test_vnc.py` (reuse the `env` fixture: `settings, db, profiles, pid`)

**Interfaces:**
- Produces: `VncRepo.sweep_orphans() -> list[int]` returning the swept `profile_id`s.

- [ ] **Step 1: Write the failing tests**

```python
async def test_sweep_orphans_fails_rows_left_by_a_dead_worker(env):
    """After a restart no process owns these rows; the port and profile are stuck."""
    _settings, db, profiles, pid = env
    repo = VncRepo(db)
    session_id = await repo.create(
        VncSession(
            profile_id=pid,
            port=6100,
            display=100,
            password="p",
            token="t",
            status=VncStatus.running,
            expires_at=utcnow() + timedelta(minutes=30),
        )
    )
    await profiles.update(pid, status=str(ProfileStatus.login))

    freed = await repo.sweep_orphans()

    assert freed == [pid]
    assert (await repo.get(session_id)).status == VncStatus.failed


async def test_sweep_orphans_leaves_finished_sessions_alone(env):
    _settings, db, _profiles, pid = env
    repo = VncRepo(db)
    session_id = await repo.create(
        VncSession(
            profile_id=pid,
            port=6100,
            display=100,
            password="p",
            token="t",
            status=VncStatus.stopped,
            expires_at=utcnow() + timedelta(minutes=30),
        )
    )

    assert await repo.sweep_orphans() == []
    assert (await repo.get(session_id)).status == VncStatus.stopped
```

- [ ] **Step 2: Run them and see them fail**

Run: `.venv/bin/python -m pytest tests/unit/test_vnc.py -q -k sweep_orphans`
Expected: FAIL — `'VncRepo' object has no attribute 'sweep_orphans'`.

- [ ] **Step 3: Implement the repo method**

```python
async def sweep_orphans(self) -> list[int]:
    """Fail sessions no live process owns and return their profile ids.

    Called once at worker startup. Only one worker may own a database
    (``WORKER_ID`` is a constant), so every unfinished row at this point
    belongs to a previous process lifetime.
    """
    unfinished = (
        VncStatus.requested.value,
        VncStatus.running.value,
        VncStatus.stopping.value,
    )
    rows = await self.db.fetchall(
        "SELECT DISTINCT profile_id FROM vnc_sessions WHERE status IN (?, ?, ?)",
        unfinished,
    )
    await self.db.execute(
        "UPDATE vnc_sessions SET status = ? WHERE status IN (?, ?, ?)",
        (VncStatus.failed.value, *unfinished),
    )
    return [row["profile_id"] for row in rows]
```

In `app.py`'s `Runtime.run_worker`, before `self.worker.run_forever(stop)`:

```python
for profile_id in await self.vnc.sessions.sweep_orphans():
    # Conditional on purpose: never free a profile a scheduled run has leased.
    await self.vnc.profiles.release_login(profile_id)
```

`VncManager` exposes both `sessions` and `profiles` already; `release_login` only touches rows still in `login`.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/unit/test_vnc.py tests/unit/test_app_wiring.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/uwp/db/repo/vnc.py src/uwp/app.py tests/unit/test_vnc.py
git commit -m "fix: fail VNC rows left behind by a dead worker"
```

---

### Task 5: Proxy handshake timeouts

**Files:**
- Modify: `src/uwp/proxybridge.py`
- Test: `tests/unit/test_proxybridge.py`

**Interfaces:**
- Produces: `PROXY_CONNECT_TIMEOUT_SECONDS = 15`.

- [ ] **Step 1: Write the failing test**

```python
async def test_a_silent_upstream_times_out_with_a_proxy_error(monkeypatch, caplog):
    """A proxy that accepts and then says nothing must not burn the run timeout."""
    monkeypatch.setattr("uwp.proxybridge.PROXY_CONNECT_TIMEOUT_SECONDS", 0.2)

    async def silent(reader, writer):
        await asyncio.sleep(5)

    server = await asyncio.start_server(silent, "127.0.0.1", 0)
    up_port = server.sockets[0].getsockname()[1]
    bridge = ProxyBridge(_proxy(port=up_port))
    await bridge.start()
    try:
        r, w = await asyncio.open_connection("127.0.0.1", bridge.port)
        w.write(b"CONNECT example.com:443 HTTP/1.1\r\n\r\n")
        await w.drain()
        assert await asyncio.wait_for(r.read(), 3) == b""
        assert "timed out" in caplog.text
    finally:
        await bridge.stop()
        server.close()
```

- [ ] **Step 2: Run it and see it fail**

Run: `.venv/bin/python -m pytest tests/unit/test_proxybridge.py -q -k silent_upstream`
Expected: FAIL — the read blocks past the 3 s `wait_for`.

- [ ] **Step 3: Implement**

Add `PROXY_CONNECT_TIMEOUT_SECONDS = 15` next to `_HEAD_LIMIT`. Wrap the three handshake awaits, each with its own handler:

- `_open_upstream`: `async with asyncio.timeout(PROXY_CONNECT_TIMEOUT_SECONDS):` around `asyncio.open_connection(...)`; `except TimeoutError: raise ProxyError("upstream proxy timed out")`.
- `_http_connect`: wrap `up_writer.drain()` **and** `up_reader.readuntil(...)`; on timeout raise `ProxyError("upstream timed out during CONNECT")`.
- `_socks5_handshake`: wrap the whole body; on timeout raise `ProxyError("upstream timed out during SOCKS5 handshake")`.

`TimeoutError` is the builtin raised by `asyncio.timeout` on 3.11+, so it is the correct clause and it does not catch `CancelledError`.

Leave `_pipe` untouched.

- [ ] **Step 4: Run the file**

Run: `.venv/bin/python -m pytest tests/unit/test_proxybridge.py -q`
Expected: PASS, including the existing handshake and TLS tests.

- [ ] **Step 5: Commit**

```bash
git add src/uwp/proxybridge.py tests/unit/test_proxybridge.py
git commit -m "fix: time out a proxy that accepts and then goes silent"
```

---

### Task 6: End both directions of a tunnel

**Files:**
- Modify: `src/uwp/proxybridge.py` (`_connect`)
- Test: `tests/unit/test_proxybridge.py`

**Interfaces:**
- Produces: no new names.

**Why not `TaskGroup`:** verified in the local interpreter, `TaskGroup` waits for every task to finish when none raises. It only cancels siblings on failure. A half-closed tunnel completes one `_pipe` cleanly, so `TaskGroup` would hang exactly like `gather`.

- [ ] **Step 1: Write the failing test**

```python
async def test_closing_one_side_of_the_tunnel_ends_the_other():
    """A half-closed tunnel must not keep a task alive for the whole run."""

    async def upstream(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()
        await reader.read()  # never sends; the client goes away first

    server = await asyncio.start_server(upstream, "127.0.0.1", 0)
    up_port = server.sockets[0].getsockname()[1]
    bridge = ProxyBridge(_proxy(port=up_port))
    await bridge.start()
    try:
        r, w = await asyncio.open_connection("127.0.0.1", bridge.port)
        w.write(b"CONNECT example.com:443 HTTP/1.1\r\n\r\n")
        await w.drain()
        assert b"200" in await r.readuntil(b"\r\n\r\n")
        w.close()
        await w.wait_closed()

        await asyncio.sleep(0.3)
        assert len(bridge._tasks) == 0
    finally:
        await bridge.stop()
        server.close()
```

- [ ] **Step 2: Run it and see it fail**

Run: `.venv/bin/python -m pytest tests/unit/test_proxybridge.py -q -k one_side`
Expected: FAIL — `len(bridge._tasks) == 1`; the upstream-bound pipe still waits.

- [ ] **Step 3: Implement**

Replace the `gather` in `_connect` with a first-completed wait that cancels the survivor:

```python
pipes = [
    asyncio.create_task(self._pipe(reader, up_writer)),
    asyncio.create_task(self._pipe(up_reader, writer)),
]
try:
    await asyncio.wait(pipes, return_when=asyncio.FIRST_COMPLETED)
finally:
    for pipe in pipes:
        pipe.cancel()
    await asyncio.gather(*pipes, return_exceptions=True)
```

Do not read exceptions out of the finished task: `_pipe` already swallows connection errors on purpose, and `_handle` owns the logging.

- [ ] **Step 4: Run the file and then the worker tests**

Run: `.venv/bin/python -m pytest tests/unit/test_proxybridge.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/uwp/proxybridge.py tests/unit/test_proxybridge.py
git commit -m "fix: end both directions of a tunnel when one side closes"
```

---

### Task 7: Remaining robustness fixes

**Files:**
- Modify: `src/uwp/proxybridge.py` (head-limit error, delete duplicate `parse_proxy_url`)
- Modify: `src/uwp/worker.py` (`run_forever`)
- Modify: `src/uwp/parsers/steam_deals/parser.py` (`_NUMBER_RE`)
- Test: `tests/unit/test_proxybridge.py`, `tests/unit/test_worker.py`, `tests/unit/test_steam_parser.py`

**Interfaces:**
- `parse_proxy_url` stays importable from `uwp.proxybridge` (existing tests import it there) but is defined in `uwp.services.proxies`.

- [ ] **Step 1: Write the three failing tests**

```python
async def test_an_oversized_request_head_gets_its_own_message(caplog):
    """``readuntil`` trips its 64 KiB default before the explicit check could."""
    bridge = ProxyBridge(_proxy())
    reader = asyncio.StreamReader()
    reader.feed_data(
        b"CONNECT example.com:443 HTTP/1.1\r\nX: " + b"a" * (70 * 1024) + b"\r\n\r\n"
    )
    reader.feed_eof()
    writer = _RecordingWriter()

    await bridge._handle(reader, writer)

    assert writer.closed
    assert "too large" in caplog.text
```

```python
async def test_run_forever_cancels_the_sibling_when_one_job_crashes(env):
    """With max_parallel_runs > 1 a crashed slot must not leave a detached task."""
    db, sites, jobs, sid = env
    await jobs.create(sid, "manual")
    await jobs.create(sid, "manual")

    worker = _worker(Settings(max_parallel_runs=2), db, jobs, sites, FakeRunner())
    sibling_cancelled = asyncio.Event()
    started = asyncio.Event()

    async def fake_process_one(now):
        # The first slot raises; the second blocks forever and must be cancelled.
        if not started.is_set():
            started.set()
            raise RuntimeError("boom")
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            sibling_cancelled.set()
            raise

    worker.process_one = fake_process_one
    stop = asyncio.Event()

    async def stop_when_done():
        await asyncio.wait_for(sibling_cancelled.wait(), 2)
        stop.set()

    with pytest.raises(ExceptionGroup):
        await asyncio.wait_for(
            asyncio.gather(worker.run_forever(stop), stop_when_done()), 5
        )
```

Note: `process_one` catches runner exceptions itself, so patching `runner.run` would never reach the loop — patch `process_one`. If the `ExceptionGroup` proves to escape differently in this harness, keep whichever version actually fails before the change and passes after.

```python
def test_parse_price_keeps_a_negative_sign():
    assert _parse_price("-4,99")[0] == -4.99
```

- [ ] **Step 2: Run them and see them fail**

Run: `.venv/bin/python -m pytest tests/unit/test_proxybridge.py tests/unit/test_worker.py tests/unit/test_steam_parser.py -q -k "oversized or sibling or negative"`
Expected: all fail.

- [ ] **Step 3: Implement**

- **Head limit:** in `_serve`, catch `asyncio.LimitOverrunError` around the `readuntil` and raise `ProxyError("request head too large")`; keep the existing `IncompleteReadError` early return. The `if len(head) > _HEAD_LIMIT` check can stay as a cheap guard, but the exception path is what actually fires.
- **`parse_proxy_url`:** delete the local definition and `import` it: `from uwp.services.proxies import parse_proxy_url`. No cycle: `services.proxies` imports `secretbox`, `config`, `db.repo.proxies` and `domain`, none of which import `proxybridge`. Keep `urlsplit`/`unquote` imports only if still used (they are, by `_parse_target`).
- **`run_forever`:** keep `asyncio.gather` but add `return_exceptions=True`, collect `ran` from the boolean results, and re-raise the first `BaseException` so the existing `except Exception` still logs and sets `ran = []`. **Not `TaskGroup`:** measured that it cancels a sibling that is legitimately mid-run, and `process_one` has no cancellation cleanup, so the sibling's claimed job row stays `running` forever (heartbeat keeps it fresh, `WORKER_ID` is constant so a restart re-adopts it, and `ux_jobs_active` blocks the site). Plain `gather` propagates the first failure immediately and leaves the other slots detached, which is the bug being fixed; `return_exceptions=True` waits for every slot and then surfaces the failure.
- **`_NUMBER_RE`:** `re.compile(r"-?\d[\d,.\s]*")`. Verified on `-4,99`: `rstrip(".,")` leaves the sign alone, `_decimal_separator` picks `,`, result `-4.99`.

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest -p no:cacheprovider`
Expected: all pass; at least the previous `384 passed, 3 skipped` plus the new tests.

- [ ] **Step 5: Lint**

Run: `.venv/bin/python -m ruff check src tests`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add src/uwp/proxybridge.py src/uwp/worker.py src/uwp/parsers/steam_deals/parser.py tests/unit/test_proxybridge.py tests/unit/test_worker.py tests/unit/test_steam_parser.py
git commit -m "fix: close the remaining review findings"
```

---

### Task 8: Verify the VNC desktop end to end

**Files:** none; verification only.

- [ ] **Step 1: Rebuild the image**

Run: `docker build -t ultimate-web-parser:latest .`
Expected: build succeeds.

- [ ] **Step 2: Start a login session and measure**

Request a login from the bot, then:

```bash
docker exec uwp sh -c 'DISPLAY=:100 xprop -root _NET_WORKAREA'
docker exec uwp sh -c 'DISPLAY=:100 wmctrl -lG | grep -i chrom'
```

Expected: `1920, 1040` and a window `1920 1040` at `0, 0`. If `xprop`/`wmctrl` are absent from the runtime image, skip to Step 3 — this step is optional instrumentation.

- [ ] **Step 3: Look at the screen**

Open the noVNC link. Expected: a taskbar strip along the bottom, Chromium filling everything above it, no grey margin on the right, no window title bar.

- [ ] **Step 4: Check the fingerprint stays coherent**

From the session, open a page that echoes geometry. Measured: `screen.height` 1080, `screen.availHeight` 1040, `innerHeight` 863, `innerWidth` 1920. Also check `_NET_FRAME_EXTENTS` is `0,0,0,0`: the `[Deco] {NONE}` rule loses a start-up race, so `defaultDeco: NONE` in `init` is what actually strips the frame.

- [ ] **Step 5: Report, do not commit**

Record the measured values in the branch summary.

---

## Self-Review

**Spec coverage:** §6.1 → Tasks 1-2. §6.2 → Task 3. §6.3 → Task 4. §6.4 → Task 5. §6.5 → Task 6. §6.6 → Task 7. §8 → Task 8.

**Corrections applied after checking the code and the image:**

1. `StateFilter(None)` was wrong in the design: `common` runs before `profiles`, so the filter would push a mid-wizard tap into the wizard handler. Handlers now always match and call `state.clear()`.
2. `--start-maximized` was removed: measured 945×1020 with `--fingerprint` present. Only `--window-size` works.
3. `TaskGroup` was wrong for the tunnel: it does not cancel on clean completion. Replaced with `wait(FIRST_COMPLETED)` plus explicit cancel.
4. The `start_server(limit=...)` change was pointless: `StreamReader`'s default is already 64 KiB, so the fix is catching `LimitOverrunError` in `_serve`.
5. Profile freeing on sweep now uses the existing conditional `release_login` instead of an unconditional `update`, so a leased profile is never stolen.
6. `TaskGroup` was wrong for `run_forever` too, for the opposite reason: it *does* cancel siblings on failure, and `process_one` has no cancellation cleanup, so a cancelled sibling's claimed job row stays `running` forever. Replaced with `gather(return_exceptions=True)`: wait for every slot, then surface the first failure.
7. The sweep excludes `requested`: in split mode the bot books that row and this worker launches it on the next tick, so sweeping it would silently kill a live booking. Only `running`/`stopping` are orphans.
8. A failing sweep is contained by `try/except Exception: log.exception(...)`. Without it, in combined mode the exception died in `_stop_tasks` and the process kept polling Telegram with no worker at all.
9. `[Deco] {NONE}` in fluxbox's `apps` alone loses a start-up race; `session.screen0.defaultDeco: NONE` in `init` is what reliably strips the frame (measured `_NET_FRAME_EXTENTS` `1,1,20,4` vs `0,0,0,0`).

**Type consistency:** `sweep_orphans() -> list[int]` is consumed only in `app.py`. `_write_fluxbox_home(home, toolbar_height) -> Path` is consumed only in the factory. `browser_flags(..., window_size=(1920, 1040))` is consumed only by the VNC factory and defaults to `None` everywhere else, so `runner.py`'s headless launches are untouched.

**Proportion:** mostly test assertions and two small templates; no task rewrites an existing module.
