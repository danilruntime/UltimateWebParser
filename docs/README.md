# Documentation

Everything in the project's README describes what it does and how to run it.
This folder holds the material behind those decisions: measurements, designs
and implementation plans written while the code was being built.

The plans are a historical record, not instructions. They are kept because the
numbers in them are the evidence for several rules in `AGENTS.md` — a future
contributor who wants to "simplify" the VNC readiness check should be able to
read why it exists.

## Images used by the READMEs

| File | What it shows |
|---|---|
| [`images/report.png`](images/report.png) | a real `.ods` report opened in LibreOffice Calc |
| [`images/vnc-desktop.png`](images/vnc-desktop.png) | the VNC desktop with Chromium, sandbox enabled |

Both were captured from the running service, not drawn: the report from 50 rows
of a real Steam run, the desktop from the same image the deployment uses.

## Design specs

A spec states the problem, the alternatives and the chosen shape *before* the
code. Russian-language; the reasoning is in the ordering of the arguments.

| Document | Covers |
|---|---|
| [`superpowers/specs/2026-11-13-ultimate-web-parser-design.md`](superpowers/specs/2026-11-13-ultimate-web-parser-design.md) | the whole service: database schema, worker loop, profiles, proxies, VNC, reports |
| [`superpowers/specs/2026-11-14-persistent-menu-and-vnc-desktop-design.md`](superpowers/specs/2026-11-14-persistent-menu-and-vnc-desktop-design.md) | the reply keyboard and the full-size VNC desktop |
| [`superpowers/specs/2026-11-16-volatile-columns-in-changes-mode-design.md`](superpowers/specs/2026-11-16-volatile-columns-in-changes-mode-design.md) | why a drifting counter must not make `changes` fire every run |

## Implementation plans

A plan turns a spec into ordered, verifiable tasks. Each one was executed
test-first; the measured symptom is quoted in the commit that closed it.

| Document | Covers |
|---|---|
| [`superpowers/plans/2026-11-13-ultimate-web-parser.md`](superpowers/plans/2026-11-13-ultimate-web-parser.md) | building the service from an empty repository |
| [`superpowers/plans/2026-11-14-persistent-menu-and-vnc-desktop.md`](superpowers/plans/2026-11-14-persistent-menu-and-vnc-desktop.md) | the permanent menu, the VNC desktop, and seven deferred defects |
| [`superpowers/plans/2026-11-15-returned-items-and-new-column.md`](superpowers/plans/2026-11-15-returned-items-and-new-column.md) | rows that vanish and come back, and the «Новое» column |
| [`superpowers/plans/2026-11-15-sandbox-and-steam-regions.md`](superpowers/plans/2026-11-15-sandbox-and-steam-regions.md) | the three seccomp relaxations, and regional Steam parsing |
| [`superpowers/plans/2026-11-16-volatile-columns-in-changes-mode.md`](superpowers/plans/2026-11-16-volatile-columns-in-changes-mode.md) | `Column(volatile=True)` |

## Elsewhere

| Document | Why it is not here |
|---|---|
| [`../AGENTS.md`](../AGENTS.md) | instructions for changing the code; kept at the root so an agent finds it |
| [`../SECURITY.md`](../SECURITY.md) | the threat model, next to the code it describes |
| [`../THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md) | licence obligations, at the root where a recipient expects them |
| [`../docker/README-seccomp.md`](../docker/README-seccomp.md) | how to rebuild the seccomp profile |
