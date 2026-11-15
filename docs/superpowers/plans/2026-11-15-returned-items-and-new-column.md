# Returned items + the «Новое» column

## Problem

Two defects, measured against the running container.

**1. The «Новое» column is missing from the delivered reports.** The live
`steam_skidki` site is in `always` mode, and `reports.py` passes the new-key set
to `write_report` only when the mode is `changes`:

```python
flagged = (
    new_keys if ExportMode(str(site.export_mode)) is ExportMode.changes else None
)
```

Measured header of `steam_skidki_steam_skidki_2026-11-15_04-28.ods`:
`['App ID','Название','Ссылка','Скидка, %',…,'Обложка']` — no `Новое`.

**2. A discount that ends and comes back is invisible.** `items` keeps exactly
one row per `(site_id, external_key)` forever and nothing marks a row as
missing. A returning row therefore compares equal to its stored payload and is
neither `new` nor `changed`. Measured with the real `Runner._save_rows`:

| run | new | changed | vanished | report (`changes`) |
|---|---|---|---|---|
| discount −50% | 730 | — | 0 | yes |
| sale ended | — | — | 1 | no |
| back, identical | — | — | 0 | **no** |

`vanished` is also wrong: it is `len(before - seen)` where `before` is *every
key ever stored*, so a 229-row history with 50 rows in the current run reports
179 vanished rows on every run after the first absence.

## Decision

Track presence, not just existence. `items.absent` is `1` for rows the last run
did not see and `0` for rows it did. A row that comes back is `new` again.

- «Новое» = "not present in the previous run" (the user chose variant 1).
- The column is written in every mode; the mode keeps deciding *whether* a file
  is written, not what it contains.
- `vanished` becomes the count of rows not seen in this run, so it no longer
  re-counts history.

## Tasks

### Task 1 — `items.absent` and `UpsertResult`

Files: `src/uwp/migrations/004_items_absent.sql` (create),
`src/uwp/db/repo/items.py`, `tests/unit/test_db.py`.

Interfaces:
- `ItemsRepo.upsert(...) -> UpsertResult` (was `bool`).
- `UpsertResult(created: bool, returned: bool)`, property `is_new`.
- `ItemsRepo.settle(site_id: int, seen: set[str]) -> int` — flags the rows
  this run did not see and returns how many disappeared *now*.

The migration adds the column and leaves existing rows `NULL`. Backfilling 0
would report the whole history as vanished on the first run after the upgrade;
backfilling 1 would report it as new.

`settle` has to handle `NULL` rows explicitly: a legacy row that is never
settled would stay `NULL` forever, so a game returning after the upgrade would
not be reported — the exact case this feature exists for. It is settled to
`absent = 1` without being counted, so the next sighting is new.

- [ ] Failing tests: created vs returned, `is_new`, `settle` counting, migration
      backfill on a database stopped at version 3, legacy-row normalisation.
- [ ] Implement migration + repo; run the file.
- [ ] Commit.

### Task 2 — runner treats a returned row as new

Files: `src/uwp/runner.py` (`_save_rows`), `tests/unit/test_runner.py`.

`_save_rows` validates and dedups every row before it touches the database,
upserts them, then calls `settle`.

- [ ] Failing tests: `[A,B] → [] → [A,B]` makes both rows new again;
      `[A,B] → [A] → [A]` reports vanished 1 then 0.
- [ ] Implement; run the file.
- [ ] Commit.

### Task 3 — the column in every mode

Files: `src/uwp/reports.py`, `tests/unit/test_reports.py`, `README.md`.

- [ ] Failing test: `always` mode writes `Новое` and flags the row.
- [ ] Drop the mode gate; document the new meaning in the README.
- [ ] Commit.

### Task 4 — verification

- [ ] `pytest` (full suite) and `ruff check src tests`.
- [ ] Rebuild and recreate the container, confirm a fresh report carries the
      column against the live `steam_skidki` site.

## Verification

Full suite `475 passed, 3 skipped`; `ruff check src tests` clean. Mutation-tested:
sourcing new keys from `result.created` only, dropping `settle`, reinstating the
mode gate, counting legacy `NULL` rows as vanished, never settling them, and
settling on a partial run — each makes the new tests fail.

Live, in the running container:

| run | rows | new | note |
|---|---|---|---|
| 40 (post-upgrade) | 20 | 0 | 209 legacy rows settled to `absent = 1`, none counted |
| 41 (two rows flagged by hand) | 20 | 2 | `Новое` = «да» for exactly those two |
| 42 (real schedule, ~11 h later) | 24 | 5 | 5 games back on sale; all five had `first_seen_at` two days earlier, so they were genuine comebacks, not new titles |

Header of the delivered report ends with `…, Платформы, Обложка, Новое`; no run
of the upgrade reported a phantom mass disappearance.

A partial run must not settle presence: its row list is truncated by the very
error that made it partial. Run 39 in the table above was written before that
guard existed; the codebase now leaves presence untouched when
`complete=False`, pinned by `test_a_partial_run_does_not_mark_unseen_rows_absent`.
