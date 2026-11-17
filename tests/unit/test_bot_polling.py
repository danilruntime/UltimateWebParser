"""A restart must not replay the commands typed against the dead process."""

from uwp.app import run_bot_only, run_combined
from uwp.bot.app import prepare_polling, run_bot
from uwp.config import load_settings


class _Session:
    async def close(self):
        return None


class _Bot:
    def __init__(self, order: list | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.order = order
        self.session = _Session()

    async def delete_webhook(self, **kwargs):
        self.calls.append(("delete_webhook", kwargs))
        if self.order is not None:
            self.order.append(("delete_webhook", kwargs))
        return True


class _Dispatcher:
    def __init__(self, order: list) -> None:
        self.order = order

    async def start_polling(self, bot):
        self.order.append(("start_polling", {}))


async def test_stale_updates_are_dropped_when_the_flag_is_on(tmp_path):
    bot = _Bot()

    await prepare_polling(bot, load_settings({"UWP_DATA_DIR": str(tmp_path)}))

    assert bot.calls == [("delete_webhook", {"drop_pending_updates": True})]


async def test_nothing_is_deleted_when_the_flag_is_off(tmp_path):
    bot = _Bot()
    settings = load_settings(
        {"UWP_DATA_DIR": str(tmp_path), "UWP_DROP_PENDING_UPDATES": "false"}
    )

    await prepare_polling(bot, settings)

    assert bot.calls == []


class _Delivery:
    async def run(self, stop):
        return None


class _Runtime:
    def __init__(self, bot: _Bot) -> None:
        self.bot = bot
        self.deps = object()
        self.admin_ids = {1}
        self.recipients = [1]
        self.delivery = _Delivery()

    async def run_worker(self, stop):
        return None

    async def close(self):
        return None


async def _record_from_run_bot(monkeypatch, tmp_path) -> list:
    order: list = []

    async def build(settings, **kwargs):
        return _Runtime(_Bot(order))

    import uwp.bot.app as bot_module

    bot = _Bot(order)
    monkeypatch.setattr(bot_module, "make_bot", lambda settings: bot)
    monkeypatch.setattr(
        bot_module, "build_dispatcher", lambda *a, **kw: _Dispatcher(order)
    )
    await run_bot(load_settings({"UWP_DATA_DIR": str(tmp_path)}), object())
    return order


async def _record_from(monkeypatch, tmp_path, function) -> list:
    order: list = []
    bot = _Bot(order)

    async def build(settings, **kwargs):
        return _Runtime(bot)

    import uwp.app as app_module

    monkeypatch.setattr(app_module, "build_runtime", build)
    monkeypatch.setattr(
        app_module, "build_dispatcher", lambda *a, **kw: _Dispatcher(order)
    )
    await function(load_settings({"UWP_DATA_DIR": str(tmp_path)}))
    return order


async def test_the_bot_deletes_the_backlog_before_it_starts_polling(
    monkeypatch, tmp_path
):
    assert await _record_from_run_bot(monkeypatch, tmp_path) == [
        ("delete_webhook", {"drop_pending_updates": True}),
        ("start_polling", {}),
    ]


async def test_the_combined_process_deletes_the_backlog_first(monkeypatch, tmp_path):
    assert await _record_from(monkeypatch, tmp_path, run_combined) == [
        ("delete_webhook", {"drop_pending_updates": True}),
        ("start_polling", {}),
    ]


async def test_the_split_bot_deletes_the_backlog_first(monkeypatch, tmp_path):
    """A call site that forgets the call replays a restart's backlog silently."""
    assert await _record_from(monkeypatch, tmp_path, run_bot_only) == [
        ("delete_webhook", {"drop_pending_updates": True}),
        ("start_polling", {}),
    ]
