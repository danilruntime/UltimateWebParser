# Third-party notices

UltimateWebParser is distributed under MIT (see [`LICENSE`](LICENSE)). Some
files in this repository were not written by us and carry other terms. What
follows is what they are, whose they are, and under which licence.

This document satisfies Apache-2.0 §4(a): a recipient of the derivative work
must receive a copy of the licence. The full text is in
[appendix A](#appendix-a-apache-license-20).

**Русская версия:** [`THIRD_PARTY_NOTICES.ru.md`](THIRD_PARTY_NOTICES.ru.md).

## Third-party files in the repository

### `docker/seccomp-uwp.json`

- **Source:** [`moby/moby`](https://github.com/moby/moby), tag `v28.0.0`,
  `profiles/seccomp/default.json`.
- **Licence:** Apache License 2.0.
- **Copyright:** Docker, Inc.
- **Modified:** yes. The Docker profile with three changed rules, without which
  Chromium cannot build its sandbox out of user namespaces.

The changes are marked in the JSON itself with a `comment` field (Apache-2.0
§4(b) requires modified files to be marked):

| Rule | In the stock profile | Here | Why |
|---|---|---|---|
| `unshare` | allowed only with `CAP_SYS_ADMIN` | new rule: `unshare(CLONE_NEWNS)` (`0x20000`) | the renderer finishes building the mount namespace |
| `clone` | mask `2114060288` (`0x7E020000`) — all namespace flags at once | mask `131072` (`0x20000`) — `CLONE_NEWNS` only | the zygote creates namespaces one flag at a time |
| `clone3` | `SCMP_ACT_ERRNO`, `errnoRet: 38` | `SCMP_ACT_ALLOW` | the same request arrives through `clone3` on a modern glibc |

The other rules are untouched. Rebuilding the profile from a fresh stock file
is described in [`docker/README-seccomp.md`](docker/README-seccomp.md).

### `tests/fixtures/steam_search.html`, `tests/fixtures/steam_search_ru.html`

- **Source:** the `store.steampowered.com/search/` page, captured with `curl` in
  an anonymous request.
- **Rightsholder:** Valve Corporation. The markup and catalogue data are theirs.
- **Licence:** none granted. The files serve as a test fixture: the parser is
  checked offline against a real page, so the test does not depend on Steam
  being reachable or on its current discounts.

This is not project code and is not distributed under MIT. Both pages are
public HTML without authentication: they contain no cookies, login tokens, or
personal data. `g_sessionID` in the fixture is an anonymous session identifier
issued to `curl`, not anyone's credentials.

If you fork the project for public use, judge for yourself whether keeping
someone else's markup in the repository is acceptable to you; it can be
replaced with synthetic HTML without losing the purpose of the tests.

### `tests/fixtures/soundcloud_playlist.html`, `tests/fixtures/soundcloud_tracks.json`

- **Source:** the `soundcloud.com/discover/sets/trending-by-genre:all-genres`
  page (server HTML captured with an anonymous `curl` request and trimmed to the
  `window.__sc_hydration` block) and its
  `api-v2.soundcloud.com/tracks?ids=…` answer for five of that playlist's tracks,
  trimmed to the fields the parser reads.
- **Rightsholder:** SoundCloud Limited and the tracks' authors. The markup,
  titles, artist names and counters are theirs.
- **Licence:** none granted. The files serve as a test fixture: the parser is
  checked offline against real data, so the test does not depend on SoundCloud
  being reachable or on its current trends.

This is not project code and is not distributed under MIT. Both files are public
data without authentication: they contain no cookies, login tokens or personal
data, only public track titles, artist names and play counts.

If you fork the project for public use, judge for yourself whether keeping
someone else's data in the repository is acceptable to you; it can be replaced
with synthetic data without losing the purpose of the tests.

## What is installed at build time but not kept in the repository

These components are not part of the sources. They only end up in the built
image, and their licences apply to the image, not to the project code.

### `fingerprint-chromium`

- **Licence:** BSD-3-Clause (Copyright 2015-2023, The ungoogled-chromium
  Authors); inside Chromium itself there are hundreds of component licences.
- **Where:** downloaded in the `Dockerfile` during the build, with the version
  and sha256 pinned. The binary is not committed to the repository.

This matters: as long as the binary is not in the repository, you are not
distributing Chromium and do not take on its licence obligations. Do not commit
`data/browser` to git.

### Debian packages (`python:3.12-slim-bookworm` + apt)

| Package | Licence | Why |
|---|---|---|
| `xvfb` (= X.Org) | MIT-like (X11) | the virtual display |
| `x11vnc` | **GPL-2.0** | the RFB server for manual login |
| `novnc` | **MPL-2.0** (+ BSD-2-Clause, OFL-1.1, CC-BY-SA-3.0) | the web client in the admin's browser |
| `websockify` | **LGPL-3.0** / MPL-2.0 | the TCP→WebSocket bridge |
| `fluxbox` | MIT (Expat) | the window manager on the display |
| `tini` | MIT (Expat) | PID 1, reaps zombies |

`x11vnc` under GPL-2.0 is the strictest of them. It does not reach the project
code: GPL-2.0 applies to derivative works, not to an aggregate. `x11vnc` runs as
a separate process through `subprocess`, talks to the rest of the stack over TCP
and the X11 protocol, and nothing from the project is linked into it. That is
aggregation, not derivation. The same argument holds for `websockify`.

The licence texts for these packages live inside the image —
`/usr/share/doc/<package>/copyright`. If you publish a built image, they travel
with it, and there is no need to copy them separately.

## What the image carries

The `Dockerfile` copies `seccomp-uwp.json` itself into the image (Apache-2.0,
modified). This document is therefore copied too — to `/opt/uwp/seccomp/`, next
to the profile. A recipient of the image receives the licence text as well, not
only a recipient of a git clone: §4(a) speaks of the recipient of the work, and
the work here is the image.

The project's `LICENSE` sits in the image at `/app/LICENSE`.

### Python dependencies

Installed from PyPI per `pyproject.toml`. Compatible with an MIT project: there
is no copyleft among them.

| Licence | Packages |
|---|---|
| MIT | aiogram, aiosqlite, pydantic, pydantic-settings, pydantic-core, attrs, annotated-types, beautifulsoup4, soupsieve, magic-filter, typing-inspection, aiosignal |
| Apache-2.0 | playwright, aiofiles, aiohttp-socks, multidict, yarl, propcache, frozenlist, python-socks, tzdata, odfpy |
| Apache-2.0 AND MIT | aiohttp |
| BSD-3-Clause | idna, pycparser, python-dotenv |
| MPL-2.0 | certifi (file-level copyleft: applies to `certifi` only) |
| PSF-2.0 / MIT AND PSF-2.0 | typing-extensions, aiohappyeyeballs, defusedxml, greenlet |
| MIT-0 | cffi |

The exact list for a particular build:
`docker compose exec worker /opt/venv/bin/pip list`.

## How to maintain this

If you add a file of someone else's authorship to `docker/` or
`tests/fixtures/`, add it here. `tests/unit/test_third_party_notices.py` checks
that every file in `docker/` other than our own is mentioned in this document,
and that a full licence text is attached for an Apache-2.0 derivative.

## Appendix A: Apache License 2.0

> **The copyright placeholder in the text below is quoted, not forgotten.**
> The line with the bracketed year and owner is the literal boilerplate of
> Appendix A of the Apache License 2.0, reproduced as part of the licence
> quotation for `docker/seccomp-uwp.json` (Copyright Docker, Inc.). It is not
> this project's copyright line and must not be filled in: doing so would
> misquote the licence the image ships. The project's own copyright line is in
> [`LICENSE`](LICENSE).

                                 Apache License
                           Version 2.0, January 2004
                        http://www.apache.org/licenses/

   TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION

   1. Definitions.

      "License" shall mean the terms and conditions for use, reproduction,
      and distribution as defined by Sections 1 through 9 of this document.

      "Licensor" shall mean the copyright owner or entity authorized by
      the copyright owner that is granting the License.

      "Legal Entity" shall mean the union of the acting entity and all
      other entities that control, are controlled by, or are under common
      control with that entity. For the purposes of this definition,
      "control" means (i) the power, direct or indirect, to cause the
      direction or management of such entity, whether by contract or
      otherwise, or (ii) ownership of fifty percent (50%) or more of the
      outstanding shares, or (iii) beneficial ownership of such entity.

      "You" (or "Your") shall mean an individual or Legal Entity
      exercising permissions granted by this License.

      "Source" form shall mean the preferred form for making modifications,
      including but not limited to software source code, documentation
      source, and configuration files.

      "Object" form shall mean any form resulting from mechanical
      transformation or translation of a Source form, including but
      not limited to compiled object code, generated documentation,
      and conversions to other media types.

      "Work" shall mean the work of authorship, whether in Source or
      Object form, made available under the License, as indicated by a
      copyright notice that is included in or attached to the work
      (an example is provided in the Appendix below).

      "Derivative Works" shall mean any work, whether in Source or Object
      form, that is based on (or derived from) the Work and for which the
      editorial revisions, annotations, elaborations, or other modifications
      represent, as a whole, an original work of authorship. For the purposes
      of this License, Derivative Works shall not include works that remain
      separable from, or merely link (or bind by name) to the interfaces of,
      the Work and Derivative Works thereof.

      "Contribution" shall mean any work of authorship, including
      the original version of the Work and any modifications or additions
      to that Work or Derivative Works thereof, that is intentionally
      submitted to Licensor for inclusion in the Work by the copyright owner
      or by an individual or Legal Entity authorized to submit on behalf of
      the copyright owner. For the purposes of this definition, "submitted"
      means any form of electronic, verbal, or written communication sent
      to the Licensor or its representatives, including but not limited to
      communication on electronic mailing lists, source code control systems,
      and issue tracking systems that are managed by, or on behalf of, the
      Licensor for the purpose of discussing and improving the Work, but
      excluding communication that is conspicuously marked or otherwise
      designated in writing by the copyright owner as "Not a Contribution."

      "Contributor" shall mean Licensor and any individual or Legal Entity
      on behalf of whom a Contribution has been received by Licensor and
      subsequently incorporated within the Work.

   2. Grant of Copyright License. Subject to the terms and conditions of
      this License, each Contributor hereby grants to You a perpetual,
      worldwide, non-exclusive, no-charge, royalty-free, irrevocable
      copyright license to reproduce, prepare Derivative Works of,
      publicly display, publicly perform, sublicense, and distribute the
      Work and such Derivative Works in Source or Object form.

   3. Grant of Patent License. Subject to the terms and conditions of
      this License, each Contributor hereby grants to You a perpetual,
      worldwide, non-exclusive, no-charge, royalty-free, irrevocable
      (except as stated in this section) patent license to make, have made,
      use, offer to sell, sell, import, and otherwise transfer the Work,
      where such license applies only to those patent claims licensable
      by such Contributor that are necessarily infringed by their
      Contribution(s) alone or by combination of their Contribution(s)
      with the Work to which such Contribution(s) was submitted. If You
      institute patent litigation against any entity (including a
      cross-claim or counterclaim in a lawsuit) alleging that the Work
      or a Contribution incorporated within the Work constitutes direct
      or contributory patent infringement, then any patent licenses
      granted to You under this License for that Work shall terminate
      as of the date such litigation is filed.

   4. Redistribution. You may reproduce and distribute copies of the
      Work or Derivative Works thereof in any medium, with or without
      modifications, and in Source or Object form, provided that You
      meet the following conditions:

      (a) You must give any other recipients of the Work or
          Derivative Works a copy of this License; and

      (b) You must cause any modified files to carry prominent notices
          stating that You changed the files; and

      (c) You must retain, in the Source form of any Derivative Works
          that You distribute, all copyright, patent, trademark, and
          attribution notices from the Source form of the Work,
          excluding those notices that do not pertain to any part of
          the Derivative Works; and

      (d) If the Work includes a "NOTICE" text file as part of its
          distribution, then any Derivative Works that You distribute must
          include a readable copy of the attribution notices contained
          within such NOTICE file, excluding those notices that do not
          pertain to any part of the Derivative Works, in at least one
          of the following places: within a NOTICE text file distributed
          as part of the Derivative Works; within the Source form or
          documentation, if provided along with the Derivative Works; or,
          within a display generated by the Derivative Works, if and
          wherever such third-party notices normally appear. The contents
          of the NOTICE file are for informational purposes only and
          do not modify the License. You may add Your own attribution
          notices within Derivative Works that You distribute, alongside
          or as an addendum to the NOTICE text from the Work, provided
          that such additional attribution notices cannot be construed
          as modifying the License.

      You may add Your own copyright statement to Your modifications and
      may provide additional or different license terms and conditions
      for use, reproduction, or distribution of Your modifications, or
      for any such Derivative Works as a whole, provided Your use,
      reproduction, and distribution of the Work otherwise complies with
      the conditions stated in this License.

   5. Submission of Contributions. Unless You explicitly state otherwise,
      any Contribution intentionally submitted for inclusion in the Work
      by You to the Licensor shall be under the terms and conditions of
      this License, without any additional terms or conditions.
      Notwithstanding the above, nothing herein shall supersede or modify
      the terms of any separate license agreement you may have executed
      with Licensor regarding such Contributions.

   6. Trademarks. This License does not grant permission to use the trade
      names, trademarks, service marks, or product names of the Licensor,
      except as required for reasonable and customary use in describing the
      origin of the Work and reproducing the content of the NOTICE file.

   7. Disclaimer of Warranty. Unless required by applicable law or
      agreed to in writing, Licensor provides the Work (and each
      Contributor provides its Contributions) on an "AS IS" BASIS,
      WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
      implied, including, without limitation, any warranties or conditions
      of TITLE, NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A
      PARTICULAR PURPOSE. You are solely responsible for determining the
      appropriateness of using or redistributing the Work and assume any
      risks associated with Your exercise of permissions under this License.

   8. Limitation of Liability. In no event and under no legal theory,
      whether in tort (including negligence), contract, or otherwise,
      unless required by applicable law (such as deliberate and grossly
      negligent acts) or agreed to in writing, shall any Contributor be
      liable to You for damages, including any direct, indirect, special,
      incidental, or consequential damages of any character arising as a
      result of this License or out of the use or inability to use the
      Work (including but not limited to damages for loss of goodwill,
      work stoppage, computer failure or malfunction, or any and all
      other commercial damages or losses), even if such Contributor
      has been advised of the possibility of such damages.

   9. Accepting Warranty or Additional Liability. While redistributing
      the Work or Derivative Works thereof, You may choose to offer,
      and charge a fee for, acceptance of support, warranty, indemnity,
      or other liability obligations and/or rights consistent with this
      License. However, in accepting such obligations, You may act only
      on Your own behalf and on Your sole responsibility, not on behalf
      of any other Contributor, and only if You agree to indemnify,
      defend, and hold each Contributor harmless for any liability
      incurred by, or claims asserted against, such Contributor by reason
      of your accepting any such warranty or additional liability.

   END OF TERMS AND CONDITIONS

   APPENDIX: How to apply the Apache License to your work.

      To apply the Apache License to your work, attach the following
      boilerplate notice, with the fields enclosed by brackets "[]"
      replaced with your own identifying information. (Don't include
      the brackets!)  The text should be enclosed in the appropriate
      comment syntax for the file format. We also recommend that a
      file or class name and description of purpose be included on the
      same "printed page" as the copyright notice for easier
      identification within third-party archives.

   Copyright [yyyy] [name of copyright owner]

   Licensed under the Apache License, Version 2.0 (the "License");
   you may not use this file except in compliance with the License.
   You may obtain a copy of the License at

       http://www.apache.org/licenses/LICENSE-2.0

   Unless required by applicable law or agreed to in writing, software
   distributed under the License is distributed on an "AS IS" BASIS,
   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
   See the License for the specific language governing permissions and
   limitations under the License.
