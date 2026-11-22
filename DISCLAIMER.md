# Disclaimer and acceptable use

UltimateWebParser automates a real browser: it opens pages on a schedule, may
hold a logged-in session, and writes what it read into a spreadsheet. That is a
general-purpose tool, and what it is pointed at decides whether using it is
legitimate. This document states the terms under which the project is offered.

**Русская версия:** [`DISCLAIMER.ru.md`](DISCLAIMER.ru.md).

## Lawful use only

Use this software only where collecting the data is lawful for you. That depends
on where you are, what you collect and what the site is: news headlines and
public prices are treated differently from personal profiles, private messages
or material behind a paywall. If you are unsure, ask a lawyer, not the tool.

The project is not a way around a restriction that applies to you. It does not
create a right to access a page, and running it does not make an unlawful
collection lawful.

## Terms of service and robots.txt

Every site has its own rules, usually in its terms of service and its
`robots.txt`. Whether those rules bind you, and whether what you are doing
complies with them, is your determination to make before the first run — not an
afterthought when an account is blocked.

The project neither reads nor enforces those rules. It does not ship a list of
"allowed" sites, and a site working with the bundled parsers is not an
endorsement: the Steam and SoundCloud parsers exist as reference
implementations, and their presence says nothing about whether your use of them
is permitted.

Respect the site's limits: keep intervals reasonable, do not run dozens of sites
through one proxy, and stop if the operator asks you to. A schedule of `45-90`
minutes exists so that a run looks like a person checking prices, not like a
crawler of the whole catalogue.

## No CAPTCHA bypass

The project contains no CAPTCHA solver, no anti-bot circumvention and no
fingerprint-forging service. The browser profile features exist to keep a
logged-in session alive between runs, not to defeat a detection system.

A CAPTCHA, a region block or an explicit "access denied" is the site refusing
automated access. The documented answer is a proxy or a manual login for a site
you are entitled to use, and the honest answer is sometimes to stop. Do not
extend this project into a bypass; that use is not supported.

## Personal data

If what you collect identifies a person — a name, a handle, a photo, an email, a
review with an author — you are processing personal data, and the obligations
that follow are yours: a lawful basis, a retention limit, a way to answer a
request to delete, and, in the EU/UK, the GDPR; in California, the CCPA. A
spreadsheet of profiles is a database of personal data whatever file extension
it has.

Reports and profiles are written under `data/`: profiles contain live cookies,
and the database contains proxy credentials. Protect that directory, back it up
if you need to, and delete it when you stop.

## Your responsibility

You are responsible for:

- the sites you point the tool at, and the schedule you give them;
- the accounts you log into, and any consequence for those accounts;
- the proxies you pay for and the traffic that goes through them;
- the data you collect, where you store it, and to whom you send it;
- complying with the law where you and the data are.

The authors provide the software, not a licence to use someone else's service.
Nothing in the project's documentation should be read as legal advice or as
permission from any site.

## No warranty

The software is provided under the MIT licence, **as is**, without warranty of
any kind. The authors are not liable for blocked accounts, lost data, legal
consequences, or damage caused by a parser reading a page differently than it
did yesterday. A page changes and a parser breaks; that is expected, and the
run fails visibly rather than silently.

## Reporting misuse

The project is not affiliated with Steam, SoundCloud or any other site it can
parse. If you believe this software is being used to harm you or a service you
run, open an issue in the repository or contact the maintainer through the
address in [`SECURITY.md`](SECURITY.md). Reports of a parser that ignores a
site's explicit refusal are welcome; requests to add a bypass are not.
