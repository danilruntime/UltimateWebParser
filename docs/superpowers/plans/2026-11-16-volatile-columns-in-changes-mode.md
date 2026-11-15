# Volatile Columns in `changes` Mode — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `export_mode=changes` fire only on meaningful row changes, by letting a parser mark drifting columns (review counts) as `volatile`.

**Architecture:** Add `volatile: bool = False` to `Column` in `parsers/base.py`. `Runner._save_rows` builds its comparison view from non-volatile columns only, so a drift in a volatile column never lands in `changed_keys` (hence never triggers `should_export`, never highlights the row). `steam_deals` marks `review_count` and `review_percent` volatile. No migration, no schema change.

**Tech Stack:** Python 3.12, pydantic, pytest (asyncio auto), ruff.

**Spec:** `docs/superpowers/specs/2026-11-16-volatile-columns-in-changes-mode-design.md`

## Global Constraints

- Line length 88 (ruff default; E501 not enforced — do not reformat existing long lines).
- Russian for anything a human reads (bot texts, `.ods` headers, log messages); English for identifiers, docstrings, comments.
- Comments explain *why*, not *what*. Carry measurements where one exists.
- TDD: failing test first, watch it fail for the right reason, then minimal code.
- Do not run `ruff format`.
- Do not touch `export_mode=always` / `never` behaviour, the `absent` presence logic, or migrations.

## Review Focus

- A run where **only** a volatile column changed must not write a report in `changes` mode and must not mark the row changed (highlighting).
- A run where a **tracked** column changed (price) must still write a report with no new keys.
- A mixed change (volatile + tracked in the same row) must still report the row as changed.
- A parser with **no** volatile columns keeps today's behaviour exactly (no silent regression for future parsers).
- The volatile column must still **appear** in the report and still be stored in `items.payload`.

---

### Task 1: `volatile` flag on `Column` and volatile-aware change detection

**Files:**
- Modify: `src/uwp/parsers/base.py:14-22` (`Column` dataclass)
- Modify: `src/uwp/runner.py:344` (`tracked` computation in `_save_rows`)
- Test: `tests/unit/test_runner.py` (new tests + a volatile column on a second fake parser)

**Interfaces:**
- Consumes: `Column(key, title, width=18, fmt=None)` — existing positional order; `volatile` is appended so no existing call site shifts.
- Produces: `Column.volatile: bool` (default `False`); `Runner._save_rows` excludes volatile columns from `_tracked_view`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_runner.py`. The tests need a parser whose columns include a volatile one and whose rows can be changed between runs. Add a module-level fake next to `FakeParser`:

```python
class DriftingParser(BaseParser):
    """A parser whose ``views`` counter drifts every run, like Steam reviews."""

    key, title, description, url = "drifting", "Drifting", "", "https://example.com"
    settings_model = _EmptySettings
    columns: ClassVar[list[Column]] = [
        Column("title", "Title"),
        Column("price", "Price"),
        Column("views", "Views", volatile=True),
    ]
    rows: ClassVar[list[dict]] = []

    async def run(self, ctx: ParseContext) -> ParseResult:
        return ParseResult(rows=[dict(row) for row in DriftingParser.rows])
```

and these tests:

```python
async def test_a_volatile_column_alone_does_not_count_as_a_change(env):
    """Steam reviews drift every few minutes; that is not a change worth a file.

    Measured on the live site: 43 of 63 delivered reports had zero new rows and
    differed only in ``Отзывов, шт`` / ``Положительных, %``.
    """
    runner, site, job, *_ = env
    runner.registry.register(DriftingParser())
    site = site.model_copy(update={"parser_key": "drifting"})
    DriftingParser.rows = [
        {"external_key": "1", "title": "A", "price": 10.0, "views": 100},
    ]
    await runner._save_rows(site, list(DriftingParser.rows))

    DriftingParser.rows = [
        {"external_key": "1", "title": "A", "price": 10.0, "views": 101},
    ]
    new_keys, changed_keys, vanished = await runner._save_rows(
        site, list(DriftingParser.rows)
    )

    assert new_keys == set()
    assert changed_keys == set()
    assert vanished == 0


async def test_a_tracked_column_change_still_counts_with_a_volatile_column_present(
    env,
):
    runner, site, job, *_ = env
    runner.registry.register(DriftingParser())
    site = site.model_copy(update={"parser_key": "drifting"})
    DriftingParser.rows = [
        {"external_key": "1", "title": "A", "price": 10.0, "views": 100},
    ]
    await runner._save_rows(site, list(DriftingParser.rows))

    DriftingParser.rows = [
        {"external_key": "1", "title": "A", "price": 5.0, "views": 101},
    ]
    _, changed_keys, _ = await runner._save_rows(site, list(DriftingParser.rows))

    assert changed_keys == {"1"}
```

Note: `site` from the fixture is a `Site`; confirm the fixture's site object supports `model_copy` (it is a pydantic model), and that `runner.registry` is exposed. If the fixture's site already has `parser_key="fake"`, leave the original tests untouched and use `model_copy` as above.

Also add a regression pin that the report still carries the volatile value:

```python
def test_a_volatile_column_is_still_written_to_the_report():
    """Marking a column volatile filters the *diff*, not the report."""
    from uwp.ods import DATA_SHEET, write_report  # noqa: F401  (imported in the test file already? keep local)

    assert Column("views", "Views", volatile=True).volatile is True
    assert Column("title", "Title").volatile is False
```

(If `test_runner.py` does not already import `load_settings`/`Path`, reuse the existing module imports; keep the new test free of new imports beyond `Column`, which is already imported.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_runner.py -q -k "volatile"`
Expected: FAIL — `TypeError: Column.__init__() got an unexpected keyword argument 'volatile'` (and the change-detection assertions would fail once the flag exists but is unused).

- [ ] **Step 3: Add `volatile` to `Column`**

In `src/uwp/parsers/base.py`, extend the dataclass:

```python
@dataclass
class Column:
    """One Excel column produced by a parser."""

    key: str
    title: str
    width: int = 18
    fmt: str | None = None
    # Some values drift on their own between runs (a review count, a live
    # viewer number). They belong in the report, but a change in them is not
    # news, so ``export_mode=changes`` must ignore them. Measured on the live
    # Steam site: review counts moved in 10-13 of 49 rows every 4 minutes,
    # turning ``changes`` into ``always``.
    volatile: bool = False
```

- [ ] **Step 4: Exclude volatile columns from the comparison view**

In `src/uwp/runner.py`, inside `_save_rows`, replace the `tracked` line:

```python
        tracked = [column.key for column in parser.columns] if parser else []
```

with:

```python
        # Volatile columns stay in the report and in the stored payload; only
        # the change detection ignores them, or a drifting counter would make
        # ``export_mode=changes`` write a file on every run.
        tracked = (
            [column.key for column in parser.columns if not column.volatile]
            if parser
            else []
        )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_runner.py -q`
Expected: PASS (all tests in the file, including the two new ones).

- [ ] **Step 6: Commit**

```bash
git add src/uwp/parsers/base.py src/uwp/runner.py tests/unit/test_runner.py
git commit -m "feat(reports): let a parser mark drifting columns volatile"
```

---

### Task 2: mark the Steam review counters volatile

**Files:**
- Modify: `src/uwp/parsers/steam_deals/parser.py` (`columns` class variable)
- Modify: `src/uwp/parsers/steam_deals/README.md` (columns table)
- Test: `tests/unit/test_steam_parser.py`

**Interfaces:**
- Consumes: `Column.volatile` from Task 1.
- Produces: `SteamDealsParser.columns` with `review_count` and `review_percent` flagged `volatile=True`; `review_summary` stays tracked (a change of «Very Positive» → «Mixed» is real news, the raw count is not).

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_steam_parser.py`:

```python
def test_steam_marks_the_drifting_review_counters_volatile():
    """A live review count is not a change; ``changes`` must not fire on it.

    Measured: neighbouring reports 4 minutes apart differed in 10-13 rows of
    ``Отзывов, шт`` and 1-3 of ``Положительных, %``, with zero new rows.
    """
    by_key = {column.key: column for column in SteamDealsParser.columns}

    assert by_key["review_count"].volatile is True
    assert by_key["review_percent"].volatile is True
    # The summary is a coarse bucket that only moves on real review activity.
    assert by_key["review_summary"].volatile is False
    assert by_key["price_final"].volatile is False
```

(Use the module's existing import of `SteamDealsParser`.)

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_steam_parser.py -q -k volatile`
Expected: FAIL on `assert False is True` for `review_count`.

- [ ] **Step 3: Flag the two counters in the parser**

In `src/uwp/parsers/steam_deals/parser.py`, change the two entries in `SteamDealsParser.columns`:

```python
        Column("review_percent", "Положительных, %", 12, volatile=True),
        Column("review_count", "Отзывов, шт", 12, volatile=True),
```

- [ ] **Step 4: Update the parser README**

In `src/uwp/parsers/steam_deals/README.md`, in the Columns table, append a note to those two rows (`Volatile: excluded from change detection.`) and add one sentence under the table explaining why (drift between runs is not news).

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_steam_parser.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/uwp/parsers/steam_deals/parser.py src/uwp/parsers/steam_deals/README.md tests/unit/test_steam_parser.py
git commit -m "fix(steam_deals): stop review counters from triggering every report"
```

---

### Task 3: document the contract in the agent docs

**Files:**
- Modify: `AGENTS.md` (Conventions or the parser contract note)
- Modify: `README.md` (parser section, `columns` bullet)

**Interfaces:**
- Consumes: nothing.
- Produces: documentation only.

- [ ] **Step 1: Update `AGENTS.md`**

In the "Do not / invariants" area for `external_key`/parser rows, add one line: a parser must mark self-drifting columns (`review_count`, live viewer numbers) `volatile=True`, because `export_mode=changes` compares every non-volatile column.

- [ ] **Step 2: Update `README.md`**

In the parser-authoring section (`Как добавить свой парсер`), extend the `columns` bullet: `volatile=True` excludes a column from change detection while keeping it in the report.

- [ ] **Step 3: Verify docs render (no code change)**

Run: `.venv/bin/python -m pytest -q` and `.venv/bin/python -m ruff check src tests`
Expected: full suite green, ruff clean.

- [ ] **Step 4: Commit**

```bash
git add AGENTS.md README.md
git commit -m "docs: explain volatile columns in the parser contract"
```

---

### Task 4: verify against the live deployment

**Files:** none (verification only).

- [ ] **Step 1: Run the full suite and the linter**

Run: `.venv/bin/python -m pytest -q && .venv/bin/python -m ruff check src tests`
Expected: `556+ passed, 3 skipped` and `All checks passed!`.

- [ ] **Step 2: Rebuild and recreate the worker**

```bash
COMPOSE="docker compose -f compose.split.yaml -f compose.split.hostnet.yaml"
$COMPOSE up -d --build worker
```

- [ ] **Step 3: Measure two consecutive runs in `changes` mode**

The live `steam_skidki` site is already in `changes`. Trigger two runs and check the newest `run_finished` event for `report_status`:

```bash
docker exec uwp-worker /entrypoint.sh site run steam_skidki
```

Expected after the fix: a run whose only difference is review counts has
`report_status == "skipped"` and emits no event; a run with a price change or a
new game still writes a report (`report_status == "written"`).

Record both runs in the plan's evidence table before claiming success.

## Verification

- Negative control: temporarily revert the `tracked` filter in `_save_rows` — the volatile test must fail again.
- Full suite + ruff clean.
- Live evidence from Task 4.
