# Third-party notices

UltimateWebParser распространяется под MIT (см. [`LICENSE`](LICENSE)). Часть
файлов в этом репозитории написана не нами, и для них действуют другие условия.
Ниже — что именно, чьё оно и по какой лицензии.

Документ закрывает требование Apache-2.0 §4(a): получатель производной работы
должен получить копию лицензии. Полный текст — в [приложении A](#приложение-a-apache-license-20).

**English version:** [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

## Сторонние файлы в репозитории

### `docker/seccomp-uwp.json`

- **Источник:** [`moby/moby`](https://github.com/moby/moby), тег `v28.0.0`,
  `profiles/seccomp/default.json`.
- **Лицензия:** Apache License 2.0.
- **Copyright:** Docker, Inc.
- **Изменено:** да. Профиль Docker с тремя изменёнными правилами, без которых
  Chromium не может собрать sandbox из user namespaces.

Изменения помечены в самом JSON полем `comment` (Apache-2.0 §4(b) требует
помечать изменённые файлы):

| Правило | В стоковом профиле | Здесь | Зачем |
|---|---|---|---|
| `unshare` | разрешён только с `CAP_SYS_ADMIN` | новое правило: `unshare(CLONE_NEWNS)` (`0x20000`) | рендерер завершает сборку mount namespace |
| `clone` | маска `2114060288` (`0x7E020000`) — все namespace-флаги сразу | маска `131072` (`0x20000`) — только `CLONE_NEWNS` | zygote создаёт namespace по одному флагу |
| `clone3` | `SCMP_ACT_ERRNO`, `errnoRet: 38` | `SCMP_ACT_ALLOW` | тот же запрос приходит через `clone3` на современной glibc |

Остальные правила не тронуты. Как пересобрать профиль из свежего стокового
файла — в [`docker/README-seccomp.md`](docker/README-seccomp.md).

### `tests/fixtures/steam_search.html`, `tests/fixtures/steam_search_ru.html`

- **Источник:** страница поиска `store.steampowered.com/search/`, снятая `curl`-ом
  анонимным запросом.
- **Правообладатель:** Valve Corporation. Разметка и данные каталога — их
  собственность.
- **Лицензия:** не предоставлена. Файлы используются как тестовая фикстура:
  парсер проверяется офлайн на реальной странице, чтобы тест не зависел от
  доступности Steam и его текущих скидок.

Это не код проекта и не распространяется под MIT. Обе страницы — публичный
HTML без аутентификации: в них нет cookie, токенов входа и персональных данных.
`g_sessionID` в фикстуре — анонимный идентификатор сессии, выданный `curl`-у, а
не чьи-либо учётные данные.

Если вы форкаете проект для публичного использования, оцените, приемлемо ли для
вас держать чужую разметку в репозитории; заменить её на синтетический HTML
можно без потери смысла тестов.

## Что устанавливается при сборке, но не лежит в репозитории

Эти компоненты не входят в исходники. Они попадают только в собранный образ, и
их лицензии действуют на образ, а не на код проекта.

### `fingerprint-chromium`

- **Лицензия:** BSD-3-Clause (Copyright 2015-2023, The ungoogled-chromium
  Authors); внутри самого Chromium — сотни лицензий его компонентов.
- **Где:** скачивается в `Dockerfile` во время сборки, версия и sha256
  зафиксированы. Бинарник в репозиторий не коммитится.

Это существенно: пока бинарник не лежит в репозитории, вы не распространяете
Chromium и не берёте на себя его лицензионные обязательства. Не коммитьте
`data/browser` в git.

### Пакеты Debian (`python:3.12-slim-bookworm` + apt)

| Пакет | Лицензия | Зачем |
|---|---|---|
| `xvfb` (= X.Org) | MIT-подобная (X11) | виртуальный дисплей |
| `x11vnc` | **GPL-2.0** | RFB-сервер для ручного входа |
| `novnc` | **MPL-2.0** (+ BSD-2-Clause, OFL-1.1, CC-BY-SA-3.0) | веб-клиент в браузере админа |
| `websockify` | **LGPL-3.0** / MPL-2.0 | TCP→WebSocket мост |
| `fluxbox` | MIT (Expat) | оконный менеджер на дисплее |
| `tini` | MIT (Expat) | PID 1, жнёт зомби |

`x11vnc` под GPL-2.0 — самый строгий из них. На код проекта это не
распространяется: GPL-2.0 срабатывает на производные работы, а не на агрегат.
`x11vnc` запускается отдельным процессом через `subprocess`, обменивается с
остальным стеком по TCP и X11-протоколу, и ничего из проекта в него не
линкуется. Это агрегация, а не деривация. Тот же довод действует и для
`websockify`.

Тексты лицензий этих пакетов лежат внутри образа —
`/usr/share/doc/<пакет>/copyright`. Если вы публикуете собранный образ, они
уезжают вместе с ним, и отдельно копировать их не нужно.

## Что образ несёт в себе

`Dockerfile` копирует в образ сам `seccomp-uwp.json` (Apache-2.0, изменён).
Поэтому этот документ тоже копируется — в `/opt/uwp/seccomp/`, рядом с
профилем. Получатель образа получает и текст лицензии, а не только получатель
git-клона: §4(a) говорит о получателе работы, а работа здесь — образ.

`LICENSE` проекта лежит в образе в `/app/LICENSE`.

### Python-зависимости

Ставятся из PyPI по `pyproject.toml`. Совместимы с MIT-проектом: копилефта среди
них нет.

| Лицензия | Пакеты |
|---|---|
| MIT | aiogram, aiosqlite, pydantic, pydantic-settings, pydantic-core, attrs, annotated-types, beautifulsoup4, soupsieve, magic-filter, typing-inspection, aiosignal |
| Apache-2.0 | playwright, aiofiles, aiohttp-socks, multidict, yarl, propcache, frozenlist, python-socks, tzdata, odfpy |
| Apache-2.0 AND MIT | aiohttp |
| BSD-3-Clause | idna, pycparser, python-dotenv |
| MPL-2.0 | certifi (файловый копилефт: касается только самого `certifi`) |
| PSF-2.0 / MIT AND PSF-2.0 | typing-extensions, aiohappyeyeballs, defusedxml, greenlet |
| MIT-0 | cffi |

Точный список для конкретной сборки:
`docker compose exec worker /opt/venv/bin/pip list`.

## Как это поддерживать

Добавили в `docker/` или `tests/fixtures/` файл не своего авторства — допишите
его сюда. `tests/unit/test_third_party_notices.py` проверяет, что каждый файл в
`docker/`, кроме наших собственных, упомянут в этом документе, и что для
Apache-2.0-производной приложен полный текст лицензии.

## Приложение A: Apache License 2.0


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
