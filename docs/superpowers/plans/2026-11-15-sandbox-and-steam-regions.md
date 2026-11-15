# Browser sandbox + Steam regional parsing

Two independent defects, each measured against the real runtime before a line
was written. Every fact below is a measurement, not a guess; the commands that
produced them are in the task briefs.

## Problem 1 — the browser runs without its sandbox

`UWP_NO_SANDBOX=true` is set in the image, and Playwright adds `--no-sandbox`
on its own unless `chromium_sandbox=True` is passed
(`playwright/driver/.../coreBundle.js:45392`). So the flag is applied twice and
the renderer is unconfined.

Measured, uid 1000, `ultimate-web-parser:latest`:

| seccomp | `--no-sandbox` | result |
|---|---|---|
| Docker default | no | `FATAL: No usable sandbox!` → exit 139 |
| Docker default | yes | renders |
| `seccomp=unconfined` | no | renders, renderer gets its own userns+pidns |
| patched profile (allows `CLONE_NEWUSER`) | no | renders, renderer isolated |

Why the default profile blocks it — three rules, each verified by removing it
and watching `No usable sandbox` come back:

| Rule | Stock | Needed |
|---|---|---|
| `clone` | matches only when every namespace flag is set (`2114060288 == 0x7E020000`) | match `CLONE_NEWNS` alone (`131072 == 0x20000`) |
| `clone3` | `SCMP_ACT_ERRNO` (ENOSYS) without `CAP_SYS_ADMIN` | `SCMP_ACT_ALLOW` |
| `unshare` | allowed only with `CAP_SYS_ADMIN` | a dedicated rule for `CLONE_NEWNS` |

My first guess was `CLONE_NEWUSER`; it was wrong. Measured: `clone` with
`NEWUSER`, `NEWUSER|NEWNS`, `NEWUSER|NEWPID` and the stock all-flags mask all
fail, while `NEWNS` alone succeeds. The relaxations stay narrow — `mount`,
`setns` and `unshare(CLONE_NEWUSER)` are still refused for uid 1000.`

In the sandboxed configuration the renderer cannot see `/data`, cannot read the
app's `/proc/<pid>/environ`, and has `NoNewPrivs: 1`.

Plan: ship `docker/seccomp-uwp.json` (Docker's default profile with exactly the
three rules above relaxed), drop `UWP_NO_SANDBOX` from the image, pass
`chromium_sandbox=True` from `browser.py`, and set
`security_opt: ["seccomp=./docker/seccomp-uwp.json"]` in both compose files.
Verified in the rebuilt image through the application's own `launch()` and
through the VNC process factory: renderer gets its own user and mount
namespace, `NoNewPrivs: 1`, cannot see `/data` or the app's environment.

## Problem 2 — Steam prices and reviews break outside `en`/`us`

Measured on live store pages for 48 regions.

**Currency.** `_CURRENCY_SYMBOLS` is a short hardcoded list, so any symbol not
in it yields `currency == ""`:

| region | price text | current | correct |
|---|---|---|---|
| ru | `317 руб` | `""` | RUB |
| pl | `19,99 zł` | `""` | PLN |
| kz | `7 040,00₸` | `""` | KZT |
| ch | `CHF 4.74` | `""` | CHF |
| in | `₹1,200.00` | `""` | INR |
| id | `Rp 227 999` | `""` | IDR |
| no | `55,00 kr` | `""` | NOK |
| za | `R 252.00` | `""` | ZAR |

Worse, any `$` prefix is read as USD, so `C$ 35.99`, `A$ 40.76`,
`Mex$ 311.99`, `NT$ 113.00`, `HK$ 34.24`, `S$23.20`, `NZ$ 39.99`,
`CLP$14.400`, `COL$ 64.800,00` all report **USD** — a wrong currency, silently.

The region cannot be used as the key: with the same `cc` the store returns
whatever wallet the session holds (`cz`/`se`/`dk`/`hu`/`ro` returned `€`,
`tr`/`ar` returned `$`). So the symbol on the page is the only honest source.

Fix: replace the symbol table with an ordered prefix/suffix table covering
every symbol observed on the 48 captured pages, longest match first, and drop
the bare `$` match for anything already matched by a longer prefix.

**Reviews.** `_REVIEW_RE` only matches English
(`97% of the 147,758 user reviews`). With `l=russian` the tooltip reads
`96% из 139,634 обзоров положительные` and both `review_percent` and
`review_count` become `None` — measured 0/50 rows on `ru`+`russian`,
`ru`+`german`, `ru`+`schinese`.

Fix: parse the structure instead of the sentence — the first `<br>`-separated
segment is the summary, the number before `%` is the percent, the largest other
number is the count. Verified against every tooltip in the 48 captured pages:
0 failures.

## Non-goals

- Translating the summary text. Steam localizes it; the column keeps the
  language the page was fetched in.
- `--no-sandbox` removal for the `uwp profile login` CLI on a host without the
  patched profile: the flag stays available for that path via settings.
