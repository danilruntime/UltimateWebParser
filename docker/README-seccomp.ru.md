# Песочница Chromium внутри контейнера

`seccomp-uwp.json` — это стоковый seccomp-профиль Docker
(`moby/moby v28.0.0`, `profiles/seccomp/default.json`), в котором изменены
**три** правила, чтобы Chromium мог собрать свою песочницу.

Файл распространяется под Apache-2.0 (Copyright Docker, Inc.) и является
изменённой работой; атрибуция и полный текст лицензии — в
[`../THIRD_PARTY_NOTICES.ru.md`](../THIRD_PARTY_NOTICES.ru.md).

**English version:** [`README-seccomp.md`](README-seccomp.md).

## Почему стокового профиля не хватает

Chromium строит песочницу из namespace'ов: zygote помещает рендерер в его
собственный mount namespace, а затем сбрасывает там привилегии. Три стоковых
правила это запрещают, и Chromium падает с:

```
FATAL:content/browser/zygote_host/zygote_host_impl_linux.cc:128
  No usable sandbox!
```

| Правило | В стоковом профиле | В чём проблема |
|---|---|---|
| `clone` | разрешён только когда **все** namespace-флаги выставлены сразу (`0x7E020000`) | Chromium просит только `CLONE_NEWNS` (`0x20000`) |
| `clone3` | `SCMP_ACT_ERRNO` (ENOSYS) без `CAP_SYS_ADMIN` | современная glibc приходит с тем же запросом через `clone3` |
| `unshare` | разрешён только с `CAP_SYS_ADMIN` | Chromium завершает сборку mount namespace вызовом `unshare(CLONE_NEWNS)` |

Все три были измерены, а не угаданы: если убрать любое из них, падение
`No usable sandbox` возвращается.

Именно поэтому образ раньше выставлял `UWP_NO_SANDBOX=true`, и поэтому рендерер
работал без изоляции — он делил user- и PID-namespace контейнера, так что
скомпрометированная страница могла прочитать `/data` (cookie, базу) и токен
бота из `/proc/<pid>/environ`.

## Что даёт исправленный профиль

Измерено в собранном образе, uid 1000, через собственный путь запуска
приложения `uwp.browser.launch()`:

| | `--no-sandbox` (было) | этот профиль (стало) |
|---|---|---|
| user namespace рендерера | контейнера | **свой** |
| mount namespace рендерера | контейнера | **свой** |
| `NoNewPrivs` | 0 | **1** |
| `/data` из рендерера | читается | **не виден** |
| `/proc/<pid>/environ` приложения | читается | **не читается** |
| `unshare(CLONE_NEWUSER)` под uid 1000 | `EPERM` | **`EPERM`** (по-прежнему) |
| `mount` / `setns` под uid 1000 | `EPERM` | **`EPERM`** (по-прежнему) |

Новые правила намеренно узкие: `clone` совпадает только по `CLONE_NEWNS`,
`clone3` остаётся ограниченным для вызывающих без `CAP_SYS_ADMIN`, а
добавленное правило `unshare` совпадает только по `CLONE_NEWNS` — то есть
`unshare(CLONE_NEWUSER)` по-прежнему запрещён. Больше в профиле ничего не
изменилось.

## Как поддерживать в актуальном состоянии

Пересобирайте, а не правьте руками:

```bash
curl -fsSL -o /tmp/default.json \
  https://raw.githubusercontent.com/moby/moby/v28.0.0/profiles/seccomp/default.json
# затем заново примените три правки и сравните с этим файлом
```

Эти правки — единственные поля `comment` в JSON, кроме стокового
`s390 parameter ordering`.

## Требования

Песочнице нужен uid ≠ 0. Контейнер и так сбрасывает привилегии до uid 1000
через `setpriv` в `entrypoint.sh`, так что это условие выполнено. Запуск образа
от root без профиля заставляет Chromium отказаться стартовать — и это честный
отказ. У операторов, которые не могут применить профиль, остаётся аварийный
выход: `UWP_NO_SANDBOX=true`.
