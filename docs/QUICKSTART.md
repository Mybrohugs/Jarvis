# JARVIS — быстрый старт и устройство проекта

[Русский](#русский) · [English](#english)

## Русский

### Описание

JARVIS — личный русскоязычный ИИ-ассистент. В этом репозитории находятся исходники настольной версии для Windows, связанного с ней PWA и отдельного мобильного PWA для iOS и Android. Мобильное приложение может работать самостоятельно; подключение к домашнему ПК — дополнительная возможность.

Это исходный код для ознакомления и разработки. Условия использования описаны в [LICENSE](../LICENSE): любое использование требует письменного разрешения автора. Репозиторий не является готовым установщиком. Ключи API и личные настройки в него не входят.

### Состав

| Путь | Назначение |
| --- | --- |
| [`developer_core/jarvis_local/`](../developer_core/jarvis_local/) | Настольные модули, локальный шлюз и связанный PWA |
| [`mobile-standalone/`](../mobile-standalone/README.md) | Мобильное PWA: Claude, Gemini Voice, Google Calendar; функции ПК опциональны |
| [`docs/JARVIS_FEATURES_1_12_RU.md`](JARVIS_FEATURES_1_12_RU.md) | Список возможностей и защит |
| [`docs/PHONE_STATION_RU.md`](PHONE_STATION_RU.md) | Настройка связанной с компьютером станции на телефоне |
| [`requirements.txt`](../requirements.txt) | Основные и дополнительные Python-зависимости |
| [`NOTICE.md`](../NOTICE.md) | Уведомления о сторонних компонентах и лицензиях |

Два мобильных клиента имеют разное назначение: PWA в `developer_core/jarvis_local/pwa/` — интерфейс для работы через шлюз JARVIS на компьютере; `mobile-standalone/` — отдельное приложение, которое обращается к облачным API напрямую.

### Запуск настольной части для разработки

Настольные модули рассчитаны на Windows и Python 3.13. Из корня репозитория в PowerShell:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$workspace = Join-Path $env:USERPROFILE 'JARVIS-Workspace'
New-Item -ItemType Directory -Force -Path $workspace | Out-Null
.\.venv\Scripts\python.exe -m developer_core.jarvis_local.cli --workspace $workspace status
```

Список команд локального CLI можно посмотреть так:

```powershell
.\.venv\Scripts\python.exe -m developer_core.jarvis_local.cli --help
```

Установленные через `requirements.txt` пакеты дают базовую среду разработки, но сами по себе не устанавливают все внешние службы и устройства. Отдельным функциям могут понадобиться Ollama и локальная модель, Tailscale, Everything, Office, микрофон, дополнительные Python-пакеты или учётные записи облачных сервисов. Подробности и ограничения описаны в основном [README](../README.md) и руководстве по телефону.

### Установка отдельного мобильного PWA

1. Разместите **содержимое каталога `mobile-standalone/`** на статическом HTTPS-хостинге. Покупать собственный домен не требуется: подойдёт бесплатный HTTPS-адрес хостинга. Не публикуйте рядом с приложением файлы с секретами.
2. Откройте адрес приложения на iPhone в Safari или на Android в Chrome и добавьте его на главный экран. Для доступа к микрофону и установки PWA нужен защищённый HTTPS-контекст.
3. Откройте **Настройки** приложения. Введите собственный ключ Claude для ответов. Ключ Gemini можно добавить для голосовой озвучки. Ключ Google Calendar не нужен: укажите OAuth Client ID из своего проекта Google Cloud и войдите в Google из приложения.
4. Если хотите связать телефон с работающим домашним компьютером, настройте JARVIS gateway и Tailscale отдельно по [руководству станции](PHONE_STATION_RU.md). В приложении задайте HTTPS-адрес компьютера и выполните сопряжение одноразовым кодом. Без включённого ПК эта дополнительная связь недоступна, автономные функции приложения остаются отдельными.

В Google OAuth зарегистрируйте HTTPS-origin и redirect URI именно того адреса, по которому открывается приложение. Используйте клиентский OAuth ID; **не помещайте OAuth client secret во фронтенд**.

### Ключи, расходы и приватность

- API-ключи вводятся в приложении пользователем. В репозитории нет встроенных ключей и бесплатный доступ к сторонним API не гарантируется.
- В мобильном приложении ключи и настройки хранятся в локальном хранилище браузера на этом устройстве. Пользуйтесь им только на доверенном устройстве, не отправляйте снимки настроек и не коммитьте ключи. Удаление данных сайта или очистка хранилища может удалить настройки.
- Запросы к Claude, Gemini и Google отправляются соответствующим сервисам; применяются их условия, лимиты и тарифы. Проверьте настройки оплаты и квоты у провайдера.
- Оболочка мобильного PWA может кэшироваться для открытия без сети, но сетевые ответы, календарь, распознавание и синтез речи без доступных сервисов не гарантируются.
- Не открывайте шлюз домашнего ПК напрямую в публичный интернет. Для подключения телефона используйте предусмотренную конфигурацию и проверяйте адрес и доступ устройства.

### Проверка перед использованием

Для настольного кода выполните набор тестов из корня репозитория:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

Часть интеграционных сценариев требует Windows, внешних приложений, оборудования или пользовательских ключей и может быть пропущена без них. Мобильное приложение дополнительно требует проверки в целевом браузере и на выбранном хостинге.

## English

### Project overview

JARVIS is a Russian-speaking personal AI assistant. This repository contains Windows desktop source, its paired PWA, and a separate iOS/Android PWA. The standalone phone app can work without the home PC; PC pairing is optional.

This is source code, not a ready-to-run installer. See the [LICENSE](../LICENSE): reuse requires the author's written permission. API keys and personal settings are not included.

### Project map

- `developer_core/jarvis_local/` — Windows desktop modules, local gateway, and paired PWA.
- `mobile-standalone/` — standalone phone PWA using Claude, optional Gemini voice, and Google Calendar.
- `docs/JARVIS_FEATURES_1_12_RU.md` — feature and safety notes.
- `docs/PHONE_STATION_RU.md` — phone-to-PC station setup.
- `requirements.txt` — core and optional Python packages; it does not install every external service.

The paired PWA uses a gateway on the home PC. The standalone PWA calls cloud services from the phone directly.

### Desktop development environment

The desktop integration targets Windows and Python 3.13. From PowerShell at the repository root:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$workspace = Join-Path $env:USERPROFILE 'JARVIS-Workspace'
New-Item -ItemType Directory -Force -Path $workspace | Out-Null
.\.venv\Scripts\python.exe -m developer_core.jarvis_local.cli --workspace $workspace status
```

See all local CLI commands with `python -m developer_core.jarvis_local.cli --help`. Additional capabilities may require Ollama and a local model, Tailscale, Everything, Office, a microphone, optional packages, or cloud accounts.

### Standalone phone app

Host the **contents** of `mobile-standalone/` on a static HTTPS host. A custom domain is not required. Open it in Safari on iOS or Chrome on Android and add it to the home screen. Enter your own Claude key, optionally a Gemini key for voice, and a Google OAuth Client ID for Calendar. Register the app's HTTPS origin and exact redirect URI in Google Cloud OAuth settings. Never put an OAuth client secret in frontend code.

Keys and settings are stored in the browser's local storage on the device. Protect the device, do not share keys, and check provider quotas and pricing. PC features need the gateway and a private connection such as the documented Tailscale setup.

### Tests

Run `python -m unittest discover -s tests` from the repository root. Some integration tests need Windows, external software, hardware, or user-provided credentials. Test the mobile app on the intended browser and HTTPS host.
