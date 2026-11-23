# Установка UltimateWebParser

UltimateWebParser — сервис в Docker: одна сборка даёт бота, воркер,
зафиксированный Chromium и стек VNC. На хосте больше ничего не ставится — ни
Python, ни Playwright, ни Chromium.

**English version:** [`install.md`](install.md).

Если вы ещё решаете, нужен ли вам этот проект, начните с
[`../README.ru.md`](../README.ru.md). Эта страница — про установку.

## Что нужно

- **Linux:** Docker Engine 24 или новее с плагином Compose v2, **Compose 2.24
  или новее**. Compose-файлы используют `env_file: required:`, и более старые
  выпуски Compose отвергают это поле с `additional property required is not
  allowed`. Проверяйте обе версии: `docker info` и `docker compose version`;
  отдельный бинарник `docker-compose` v1 — не то, что используют команды ниже.
- **Windows 10 или 11:** Docker Desktop с включённым бэкендом **WSL 2**. На
  текущих версиях Docker Desktop сам ставит WSL 2; дистрибутив Ubuntu
  устанавливается отдельно.
- **x86_64 (amd64).** Браузер — сборка под x86_64 Linux; на ARM не работает.
- Около 4 ГБ свободной RAM и 3 ГБ диска под образ, браузер и один профиль.
- Свободные порты 6000–6100 на loopback-интерфейсе хоста. Они публикуются
  только на `127.0.0.1`, поэтому открывать их в файрволе не нужно.
- Токен бота от [@BotFather](https://t.me/BotFather) и ваш Telegram id.

Сервер без графической оболочки подходит: рабочий стол VNC существует внутри
контейнера, а открываете вы его в своём браузере.

## Linux

### Подготовить файлы

```bash
git clone https://github.com/danilruntime/UltimateWebParser
cd UltimateWebParser
cp .env.example .env
```

Откройте `.env`. Две строки обязательны:

```bash
UWP_BOT_TOKEN=123456789:AA...        # от @BotFather
UWP_ADMIN_IDS=111,222                # ваш Telegram id; через запятую
```

Третью настоятельно стоит задать до первого прокси или входа. Сгенерируйте
ключ и вставьте в `.env` его **вывод**:

```bash
openssl rand -hex 32
```

```bash
UWP_SECRET_KEY=9f2c...      # строка, напечатанная выше, как есть
```

`.env` — обычный файл, который читает контейнер, а не shell-скрипт: запись
`UWP_SECRET_KEY=$(openssl rand -hex 32)` сохранит этот текст как сам ключ —
23 символа, напечатанные на этой странице, которые проходят любую проверку
длины и шифруют секреты так, что их расшифрует любой, кто эту страницу прочёл.
(`${...}` Compose как раз раскрывает, поэтому `UWP_SECRET_KEY=${MY_KEY}`
сработает; не работает только shell-синтаксис.)

Без неё пароли прокси и VNC лежат в `data/uwp.db` открытым текстом, и
`uwp doctor` об этом предупреждает. Если задать ключ *после* того, как пароли
уже сохранены, они станут нечитаемыми — пути перешифровки нет by design.

Остальное необязательно и описано прямо в `.env.example`. Полный справочник —
в таблице настроек README.

### Запустить стек

Split-режим — рекомендуемое развёртывание: бот опрашивает Telegram, воркер
запускает браузер.

```bash
docker compose -f compose.split.yaml -f compose.split.hostnet.yaml up -d --build
```

Второй файл переводит бота в сеть хоста; он нужен только когда
`UWP_TELEGRAM_PROXY` указывает на `127.0.0.1` хоста, то есть на прокси,
запущенный на той же машине. Такого прокси нет — второй файл не нужен:

```bash
docker compose -f compose.split.yaml up -d --build
```

Можно и одним контейнером — именно его поднимает голый `docker compose up`:

```bash
docker compose up -d --build
```

Combined-режим проще и обходится одним файлом, но перезапуск бота прерывает
прогон, а процесс, запускающий браузер, держит и токен бота. Чем режимы
различаются, написано в README.

Первая сборка скачивает Chromium (около 200 МБ) и занимает несколько минут.

### Проверить, что работает

Флаги `-f` нужны в каждой команде: голый `docker compose` читает
`compose.yaml`, то есть поднимает одиночный контейнер. Сервис называется
`worker`, а не `uwp-worker` — `uwp-worker` это имя контейнера, а `exec` ждёт
имя сервиса.

```bash
compose="docker compose -f compose.split.yaml -f compose.split.hostnet.yaml"
$compose ps                        # оба сервиса "healthy"
$compose logs -f bot               # лог самого бота
$compose exec worker /entrypoint.sh doctor --worker
```

`--worker` здесь не опционален: сервис воркера намеренно работает с пустым
`UWP_BOT_TOKEN`, поэтому проверка без флага сообщает об отсутствующем токене и
выходит с ненулевым кодом на здоровом развёртывании. Чтобы проверить токен,
запустите обычный `doctor` внутри `bot`.

`doctor` проверяет каталоги данных, бинарник браузера и его запуск, стек VNC
(предупреждение, если нет Xvfb или x11vnc), токен, админов, ключ шифрования и
миграции. При поломке он выходит с ненулевым кодом.

В combined-режиме `uwp` называется и сервис, и контейнер:

```bash
docker compose exec uwp /entrypoint.sh doctor
```

Отправьте боту `/start`. Если он молчит, значит не достаёт до
`api.telegram.org`: задайте `UWP_TELEGRAM_PROXY` и перезапустите. Подробности о
прокси — в [`README.ru.md#the-telegram-proxy-in-detail`](README.ru.md#the-telegram-proxy-in-detail).
EN: [`README.md#the-telegram-proxy-in-detail`](README.md#the-telegram-proxy-in-detail).

## Windows (Docker Desktop и WSL 2)

Windows запускает те же Linux-контейнеры. Docker Desktop даёт движок и его
бэкенд WSL 2, поэтому с точки зрения контейнера ядро — Linux.

### Поставить предварительные требования

1. Установите [Docker Desktop](https://www.docker.com/products/docker-desktop/) и
   выберите бэкенд **WSL 2** в настройках. Виртуализация должна быть включена в
   BIOS/UEFI.
2. Поставьте WSL 2 и дистрибутив Ubuntu из PowerShell с правами администратора:

```powershell
wsl --install -d Ubuntu
```

3. Откройте **Ubuntu** из меню «Пуск». Все команды ниже выполняются в этом
   терминале; Docker Desktop сам открывает движок для него, поэтому
   `docker compose version` там работает.
4. В Docker Desktop включите **Settings → Resources → WSL integration** для
   этого дистрибутива.

### Положить код в WSL

Клонируйте внутрь файловой системы WSL, а не на диск Windows:

```bash
cd ~
git clone https://github.com/danilruntime/UltimateWebParser
cd UltimateWebParser
cp .env.example .env
nano .env
```

`/mnt/c/...` работает, но медленно: каждая файловая операция идёт через
файловую систему 9p, а Chromium и SQLite делают их тысячами; права файлов там
эмулируются. Профиль к тому же хранит живые cookies, которые проект защищает
POSIX-правами, — настоящими они бывают только на стороне Linux. Держите
репозиторий и `data/` в `~/`.

Строки `.env` те же, что в Linux: `UWP_BOT_TOKEN`, `UWP_ADMIN_IDS` и
`UWP_SECRET_KEY` — вывод `openssl rand -hex 32`, вставленный в `.env`, а не
подстановка `$(...)` (`nano .env` заменяет им строку-пример).

### Запустить

```bash
docker compose -f compose.split.yaml up -d --build
```

На Docker Desktop оговорка про сеть хоста не нужна: прокси, слушающий на
стороне Windows, доступен из контейнера как `host.docker.internal`, поэтому
`UWP_TELEGRAM_PROXY=socks5://host.docker.internal:1080` работает с обычным
`compose.split.yaml`. Оверлей `compose.split.hostnet.yaml` — ответ Linux на
прокси, слушающий на `127.0.0.1` хоста; здесь его добавлять не нужно, если
боту не требуется именно сетевой namespace WSL.

Если Docker Desktop только что обновился, дайте движку пару секунд до начала
сборки; `docker info` должен отвечать без ошибки.

### Достучаться до бота и noVNC из Windows

Windows пробрасывает `localhost` в движок Docker, поэтому ссылку VNC, которую
присылает бот (`http://localhost:6001/vnc.html`), можно открыть в Edge или
Chrome прямо в Windows без настройки. Логи и `doctor` тоже работают из
терминала Ubuntu — с теми же флагами `-f`, что и выше. Без флагов или без имени
сервиса вы получите пустой вывод, а не split-режим:

```bash
docker compose -f compose.split.yaml logs -f bot
docker compose -f compose.split.yaml exec worker /entrypoint.sh doctor --worker
```

Если ссылка не открывается, проверьте, что сессия есть
(`docker compose -f compose.split.yaml exec worker /entrypoint.sh vnc list`), и
что порт в ссылке попадает в опубликованный диапазон 6000–6100. Windows может
зарезервировать часть этого диапазона под Hyper-V; список резервов показывает
`netsh interface ipv4 show excludedportrange protocol=tcp`, и после перезагрузки
он меняется — это единственный отказ, специфичный для Windows.

`UWP_DATA_DIR` остаётся `data`: bind-mount `./data:/data:z` разрешается внутри
WSL, а не на `C:`.

## Почему нативный запуск в Windows не поддерживается

Сборки под Windows проект не выпускает, и это решение, а не недостающая
функция:

- **Браузер — бинарник Linux.** `fingerprint-chromium` зафиксирован по sha256
  как архив x86_64 Linux ELF; `uwp browser install` скачивает именно его и ждёт
  внутри `chrome`. Windows его не выполнит.
- **Стек VNC — Linux.** Xvfb, x11vnc, fluxbox и websockify — отдельные
  Linux-программы; окно входа — это дисплей X11. Замены под Windows нет, а
  киоск опирается на перехват сочетаний клавиш в fluxbox.
- **Песочница — это seccomp-профиль Docker.** `docker/seccomp-uwp.json` разрешает
  Chromium создавать свои namespace. Без него браузер не стартует; файл — это
  политика seccomp-bpf для Linux, а не список доступа Windows.
- **Entrypoint — скрипт Linux.** Он один раз работает от root, меняет владельца
  тома на `1000:1000`, сбрасывает привилегии через `setpriv` и полагается на
  `chmod`, `umask` и `PR_SET_DUMPABLE`, чтобы база, WAL и окружение процесса не
  достались браузеру. Ничего этого в Windows нет.
- **Код использует POSIX-примитивы.** `fcntl.flock` выстраивает миграции в
  очередь между ботом и воркером; права `0700`/`0600` защищают cookies и
  пароли. Точных аналогов в Windows нет.

WSL 2 — не порт всего этого, а ядро Linux, которое запускает Linux-контейнер,
с Docker Desktop сверху. Поэтому так работает, а нативный запуск — нет.

## Первые шаги

1. Отправьте `/start`. Бот создаст базу и заведёт один **выключенный** сайт —
   «Steam — deals» на `en`-развёртывании, «Steam — скидки» на `ru`.
2. Откройте «🌐 Сайты», нажмите «🟢 Включить» на этой карточке. Первый прогон
   встанет в очередь сразу, а отчёт `.ods` придёт в чат.
3. Если сайту нужна конкретная страна, добавьте прокси в «🌍 Прокси» и
   привяжите его к сайту в «⚙️ Настройки».
4. Для сайта со входом создайте профиль в «👤 Профили», привяжите его к сайту и
   нажмите «🖥 Войти в профиль». Ссылка и пароль придут сообщением; войдите и
   нажмите «✅ Готово».

Отчёты появляются в `data/exports/<slug-сайта>/` на хосте, хранятся последние
50 на сайт.

## Обновление

Флаги `-f` те же, с которыми вы запускали. Блок ниже — split-развёртывание с
оверлеем для loopback-прокси; в Windows и на любом хосте без host networking
уберите `-f compose.split.hostnet.yaml`, как убирали при запуске:

```bash
git pull
docker compose -f compose.split.yaml -f compose.split.hostnet.yaml up -d --build
```

Миграции только добавляются и применяются при старте, поэтому `data/` остаётся
на месте — база, профили с cookies и отчёты. Откат не поддерживается: старый
образ откажется запускаться с базой, которую мигрировал новый
(`schema version N ... a downgrade is not supported`).

Перед мажорным обновлением сделайте копию `data/`. Это каталог обычных файлов,
и `down` его не удаляет — но `down` тоже требует те же флаги `-f`, иначе он не
найдёт ни одного сервиса.

## Удаление

```bash
docker compose -f compose.split.yaml -f compose.split.hostnet.yaml down
rm -rf data
```

`down` убирает контейнеры; в `data/` лежат база, профили браузера с живыми
cookies, отчёты и логи. Удаление необратимо, поэтому сначала выгрузите то, что
нужно сохранить.

Образ остаётся в кеше, пока его не удалить. Его собирают базовые compose-файлы
(`compose.yaml` и `compose.split.yaml`) под именем
`ultimate-web-parser:latest`; два оверлея `hostnet` добавляют только сеть:

```bash
docker image rm ultimate-web-parser:latest
```

## Что читать дальше

| Документ | На какой вопрос отвечает |
|---|---|
| [`../README.ru.md`](../README.ru.md) | что делает проект, как устроен, таблица настроек |
| [`README.ru.md`](README.ru.md) | руководство: права, прокси Telegram, VNC, отчёты, CLI, свой парсер |
| [`../SECURITY.ru.md`](../SECURITY.ru.md) | модель угроз и известные ограничения |
| [`../DISCLAIMER.ru.md`](../DISCLAIMER.ru.md) | законное использование, условия сайтов и ответственность оператора |
| [`../docker/README-seccomp.ru.md`](../docker/README-seccomp.ru.md) | три послабления seccomp и как пересобрать профиль |
