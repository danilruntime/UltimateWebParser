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
- `ItemsRepo.mark_all_absent(site_id: int) -> None`.
- `ItemsRepo.absent_count(site_id: int) -> int`.

The migration adds the column and backfills `absent = 1` for every existing
row: no earlier run recorded presence, so claiming otherwise would make the
first post-migration run report every historical row as vanished.

- [ ] Failing tests: created vs returned, `is_new`, absent reset by upsert,
      `absent_count`, migration backfill on a database stopped at version 3.
- [ ] Implement migration + repo; run the file.
- [ ] Commit.

### Task 2 — runner treats a returned row as new

Files: `src/uwp/runner.py` (`_save_rows`), `tests/unit/test_runner.py`.

`_save_rows` validates and dedups every row before it touches the database,
then calls `mark_all_absent`, upserts, and reads `absent_count`.

- [ ] Failing tests: `[A,B] → [] → [A,B]` makes both rows new again;
      `[A,B] → [A] → [A]` reports vanished 0 on the third run.
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
