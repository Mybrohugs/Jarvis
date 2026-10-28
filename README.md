# J.A.R.V.I.S. — личный ИИ-ассистент / personal AI assistant

[Русский](#русский) · [English](#english)

> **© 2026 Roman Makhar. Код открыт для просмотра, а использовать его можно только с моего письменного разрешения.**
> Запросить разрешение: [Issue «Permission request»](https://github.com/Mybrohugs/jarvis/issues/new?title=Permission%20request%20%2F%20%D0%97%D0%B0%D0%BF%D1%80%D0%BE%D1%81%20%D1%80%D0%B0%D0%B7%D1%80%D0%B5%D1%88%D0%B5%D0%BD%D0%B8%D1%8F) · [LICENSE](LICENSE)
>
> **© 2026 Roman Makhar. Source available for viewing; any use requires my written permission.**
> Ask via an [Issue "Permission request"](https://github.com/Mybrohugs/jarvis/issues/new?title=Permission%20request%20%2F%20%D0%97%D0%B0%D0%BF%D1%80%D0%BE%D1%81%20%D1%80%D0%B0%D0%B7%D1%80%D0%B5%D1%88%D0%B5%D0%BD%D0%B8%D1%8F) · [LICENSE](LICENSE)

> Русскоязычный ассистент для Windows и iPhone: понимает речь, управляет компьютером и домашней техникой,
> говорит своим голосом, запоминает хозяина и подстраивается под него. Каждое действие подтверждается,
> проверяется, пишется в журнал и может быть отменено.

---

## Русский

### Что умеет JARVIS

**Понимает, что вы хотите**
- Русские команды и свободную речь. Сначала их разбирают быстрые правила (набор из 67 фраз, 67/67), а что правила не поняли, понимает локальная модель Qwen3 8B.
- Защита от «вредных» подсказок в духе CaMeL:
  - модель видит только вашу фразу;
  - опасные слова отсекаются до неё («удали», «пароль», «переведи деньги»);
  - в плане допускаются только известные задачи;
  - каждый путь и текст в плане должен дословно встречаться во фразе.
- Многошаговые задачи: «найди отчёт, сделай копию и открой» — с планом, подтверждением, проверкой результата и откатом («отмени последнее действие»).

**Компьютер**
- Файлы: копирование, перемещение, переименование. Ничего не перезаписывает, ничего не удаляет: при откате файл уходит в карантин.
- Word и Excel правит только в копиях, оригиналы не трогает.
- Мгновенный поиск по всему диску через Everything. Если Everything завис, JARVIS сам его перезапускает.
- Окна Windows: тестовое окно (UI Automation) и Блокнот (своя вкладка, самопроверка). Снимок экрана — на iPhone.
- Браузер: чтение страниц в изолированном Chromium, только с разрешённых сайтов.

**Дом**
- Телевизор Sony Bravia, саундбар HT-S400 (через HDMI ARC) и PlayStation 5 (через HDMI-CEC).
- Сценарии «режим игры», «режим кино», «спокойной ночи». Перед сценарием JARVIS объявляет его («Сэр, включаю режим игры»), после — отчитывается.
- Запоминает вашу любимую громкость для каждого сценария.

**iPhone — мини-станция**
- Приложение на экране «Домой» (PWA) в стиле HUD. Подключается через приватную сеть Tailscale, вход по одноразовому 6-значному коду.
- Быстрые команды по разрешённому списку: громкость (не выше 40), звук, сценарии, файлы. Не больше 20 команд в минуту.
- Push-уведомления и утренний брифинг: погода, дела, увлечения по выходным.
- Файлы с компьютера по одноразовой ссылке, общий буфер обмена, снимок экрана.

**Голос**
- «Эй, Джарвис» у компьютера: распознавание слова-пробуждения (openWakeWord), речь — Whisper.
- Голос JARVIS — Schedar (Gemini TTS) с эффектом «как в фильме»: чистый низ, лёгкое металлическое эхо, цифровое удвоение и «робот». Голос и силу эффекта выбрал хозяин по образцам.
- Без интернета частые фразы звучат локальным голосом. Его создала модель Qwen3-TTS по описанию: «молодой британский дворецкий с улыбкой». Чужие записи для этого не использовались. Фразы записаны заранее и звучат мгновенно.

**Помнит вас и подстраивается под настроение**
- Помнит то, что вы разрешили: как к вам обращаться, увлечения, что нравится и не нравится, стиль ответов.
- Сам замечает привычки: в какое время и в какие дни вы обычно что делаете. Предлагает привычное («Обычно в это время вы включаете режим игры — включить?»), но сам ничего не запускает.
- **Угадывает настроение по тому, как вы говорите:**
  - короткие отрывистые команды → отвечает короче, без лишних слов;
  - раздражение («опять не то», «да что ж такое», повтор той же команды) → «Понял, исправляюсь» и чётко по делу;
  - вежливость и тепло → отвечает теплее.

  Это анализ слов и манеры команд, а не распознавание эмоций по голосу или лицу.
- Никогда не хранит здоровье, религию, политику, интимное, пароли, номера карт и документов, даже если попросить. «Забудь всё обо мне» стирает всё.

**Выступления и презентации**
- «Подготовь выступление про … на 10 минут». Claude (Opus 5.5) пишет текст и сценарий слайдов, компьютер собирает:
  - текст выступления в Word;
  - три презентации PowerPoint: HUD в стиле JARVIS, деловая, креативная;
  - в заметках докладчика под каждым слайдом — что именно говорить.
- Картинки по смыслу примерно на трети слайдов, остальные слайды чистые.
- Не выдумывает цифры и цитаты: где фактов нет — пометка «[уточнить]» и список «проверить перед выступлением».

**Каждый день**
- Напоминания и таймеры голосом и с телефона: «напомни через 20 минут…», «в 18:30…», «завтра в 9…», «каждую пятницу в 10…», «поставь таймер на 5 минут». В срок JARVIS говорит вслух и присылает push.
- Музыка и видео в любом плеере (Spotify, Яндекс Музыка, YouTube в браузере, VLC): «пауза», «продолжи», «следующий трек», «что играет?».
- Умный дом Яндекса: «включи свет», «погаси свет на кухне», «свет на 40%», «включи розетку», «запусти сценарий кино», «что включено дома».
- Режимы дома: «я дома» (свет и музыка на колонке), «я ушёл» (выключить всё, включая телевизор), «доброе утро» (свет 30%, колонка: громкость 4 и плейлист), «добрый вечер» (свет 60%, тёплый оттенок). Колонку включает одноимённый сценарий Яндекса; на iPhone есть кнопки «Я дома» и «Я ушёл».
- Свежие ответы из интернета: «найди в интернете…», «что нового про…». Поиск через свой SearXNG без ключей и без истории в Google, пересказ локальной моделью, ссылки на источники.
- «Что на экране?»: локальная модель, которая понимает картинки, читает экран и дословно цитирует ошибки. Снимок экрана не покидает компьютер.
- Диктовка в любое окно: держите F9, говорите, отпускаете — текст вставлен. Слова-паразиты убираются, есть свой словарь.
- Программы: «открой эксель / ворд / телеграм», «открой папку загрузки», «открой ютуб», «открой второй файл», «сверни все окна». Только из разрешённого списка, после запуска окно проверяется.
- Перебивание: «Эй, Джарвис» посреди ответа — JARVIS замолкает и слушает новую команду.

**Следит за собой**
- `doctor` каждый час проверяет 13 своих систем, о поломках присылает push на iPhone.
- Пишет хозяину письменные отчёты в Telegram.

### Эта публичная версия
Здесь только модуль JARVIS Local (`developer_core/jarvis_local`). Модули полной приватной сборки JARVIS DEV сюда не входят:
- драйвер телевизора Sony;
- Telegram;
- операции с файлами и Office через V8;
- браузерный движок.

Функции, которым они нужны, здесь честно сообщают «не установлено».

### Безопасность
- **Trusted Local Session:** опасные действия подтверждаются кодом с клавиатуры компьютера. Телефон и голос могут выполнять только короткий разрешённый список.
- Ничего не зарегистрировано во внешних точках входа (Telegram, MCP, мобильный шлюз) — проверяется автоматически.
- Ключи (Gemini, Claude, Pexels, Sony, Telegram, Яндекс) хранятся зашифрованными DPAPI Windows и нигде не выводятся.
- Журнал `audit.jsonl`: каждое действие, отказ и ошибка; откат по SHA-256.
- В репозитории нет ключей, записей голоса, моделей, личного профиля и адресов домашней сети.

### Проверки
- В этой версии 147 автотестов (`python -m unittest discover -s tests`): 133 проходят, 14 пропускаются без закрытого кода или реального оборудования. В полной приватной сборке их 459. Модульные тесты, тесты с подменой внешних сервисов, проверка набора русских команд.
- Проверено на реальной системе:
  - клик в тестовом окне и самопроверка Блокнота;
  - чтение телевизора и изменение громкости через очередь с телефона;
  - `doctor` и поиск Everything;
  - голос Gemini и переход на локальный голос без интернета;
  - выступление через Claude с презентациями (проверены через PowerPoint);
  - ответ из интернета через SearXNG, «что на экране» на реальном экране, запуск программы с проверкой окна;
  - умный дом Яндекса на настоящих устройствах: розетка, лампочка, выключатель, яркость, тёплый свет и все четыре режима дома.
- Ещё не проверено:
  - влияет ли регулировка басов на HT-S400;
  - живая команда «Эй, Джарвис»;
  - push на настоящий iPhone;
  - картинки из Gemini (нужен платный тариф) и Pexels (нужен ключ);
  - живые диктовка и перебивание.

### Где что лежит
Код: [`developer_core/jarvis_local/`](developer_core/jarvis_local/) · Функции 1–12: [`docs/JARVIS_FEATURES_1_12_RU.md`](docs/JARVIS_FEATURES_1_12_RU.md) · Телефон: [`docs/PHONE_STATION_RU.md`](docs/PHONE_STATION_RU.md) · Сторонние компоненты: [`NOTICE.md`](NOTICE.md)

---

## English

### What JARVIS can do

**Understands you**
- Russian commands and free speech: fast rules first (a 67-phrase set, 67/67), then a local Qwen3 8B model for whatever the rules miss.
- CaMeL-style injection guards:
  - the model sees only your phrase;
  - dangerous words are cut before it (delete, password, transfer money);
  - only known tasks are allowed in a plan;
  - every path and text in a plan must appear verbatim in the phrase.
- Multi-step tasks ("find the report, copy it and open it") with a plan, approval, result verification and rollback ("undo the last action").

**Computer**
- Files: copy, move, rename. Never overwrites, never deletes: rollback moves files to quarantine.
- Word and Excel are edited on copies only.
- Whole-disk search via Everything, which JARVIS restarts itself when it hangs.
- Windows UI: a test window (UI Automation) and Notepad (own tab, self-test). Screenshot to the iPhone.
- Browser: reads pages in an isolated Chromium, allowed sites only.

**Home**
- Sony Bravia TV, HT-S400 soundbar (HDMI ARC) and PlayStation 5 (HDMI-CEC).
- "Game", "movie" and "good night" scenes. JARVIS announces a scene before it runs ("Sir, switching to game mode") and reports after.
- Remembers your favourite volume per scene.

**iPhone mini-station**
- Home-screen app (PWA) with a HUD theme, reached over a private Tailscale network and paired with a one-time 6-digit code.
- Allow-listed quick commands: volume (max 40), mute, scenes, files. At most 20 commands per minute.
- Push notifications and a morning briefing: weather, plans, hobbies on weekends.
- One-time file links from the PC, shared clipboard, screenshots.

**Voice**
- "Hey JARVIS" at the PC: openWakeWord wake word, Whisper speech recognition.
- JARVIS speaks with Gemini TTS voice Schedar plus a film-style effect: clean low end, a slight metallic room, a digital double and a "robot" timbre. The owner chose the voice and effect strength from samples.
- Offline, frequent replies use a local voice that Qwen3-TTS designed from a text description ("a young, smiling British butler"), with no real person's recordings. These phrases are pre-recorded and play instantly.

**Remembers you and adapts to your mood**
- Remembers what you allow: how to address you, hobbies, likes and dislikes, reply style.
- Learns habits by itself: when and on which days you usually do what. It suggests them ("You usually start game mode around now, shall I?") but never runs anything on its own.
- **Reads your mood from how you speak:**
  - short, clipped commands → shorter replies;
  - frustration ("wrong again", repeating the same command) → "Got it, fixing that" and straight to the point;
  - polite and warm phrasing → warmer replies.

  This is analysis of wording and command patterns, not emotion recognition from voice or face.
- Never stores health, religion, politics, intimate life, passwords, card or document numbers, even if asked. "Forget everything about me" erases it all.

**Talks and presentations**
- "Prepare a 10-minute talk about …". Claude (Opus 5.5) writes the speech and the slide script; the PC builds:
  - the speech as a Word document;
  - three PowerPoint decks: JARVIS HUD, business, creative;
  - speaker notes on every slide with what to say.
- Meaningful pictures on about a third of the slides.
- No invented numbers or quotes: missing facts become "[уточнить]" and go to a "check before the talk" list.

**Every day**
- Reminders and timers by voice or phone: "in 20 minutes…", "at 18:30…", "tomorrow at 9…", "every Friday at 10…", "set a 5-minute timer". When due, JARVIS says it aloud and pushes it to the phone.
- Music and video in any player (Spotify, Yandex Music, YouTube in a browser, VLC): pause, play, next, previous, "what's playing?".
- Yandex smart home: lights on/off per room, brightness, sockets, the owner's scenarios, "what's on at home".
- Home routines: "I'm home" (light + music on the speaker), "I left" (everything off, TV included), "good morning" (light 30%, speaker volume 4 + playlist), "good evening" (light 60%, warm white). The speaker part runs through a same-named Yandex scenario; the iPhone has "I'm home" / "I left" buttons.
- Fresh answers from the internet: own SearXNG (no API keys, no search history at Google), summary by a local model, source links.
- "What's on my screen?": a local vision model reads the screen and quotes errors verbatim. The screenshot never leaves the PC.
- Dictation into any window: hold F9, speak, release. Fillers are removed, and there is a personal dictionary.
- Programs: open Excel, Word, Telegram, known folders and sites, or a file found earlier; show the desktop. Allowlist only, and the window is checked after launch.
- Barge-in: say "Hey JARVIS" during a reply and it stops and listens.

**Looks after itself**
- `doctor` checks 13 subsystems every hour and pushes a notification when something breaks.
- Sends written reports to the owner via Telegram.

### This public edition
Only the JARVIS Local module (`developer_core/jarvis_local`) is here. Modules of the full private JARVIS DEV build are not included:
- the Sony TV driver;
- Telegram;
- V8 file and Office operations;
- the browser engine.

Features that need them report "not installed".

### Security
- **Trusted Local Session:** risky actions need a code typed at the PC keyboard. Phone and voice can run only a short allow-list.
- Not registered with any remote entry point (Telegram, MCP, mobile gateway); this is checked automatically.
- Keys (Gemini, Claude, Pexels, Sony, Telegram, Yandex) are encrypted with Windows DPAPI and never printed.
- An `audit.jsonl` journal records every action, refusal and failure; rollback is verified by SHA-256.
- The repository holds no keys, voice recordings, models, personal profile data or home network addresses.

### Testing
- 147 automated tests in this edition (`python -m unittest discover -s tests`): 133 pass, 14 are skipped without the private modules or real hardware. The full private build has 459. Unit tests, tests with external services replaced by fakes, and the Russian command set.
- Verified on the real system:
  - a click in the test window and the Notepad self-test;
  - reading the TV and changing the volume through the phone queue;
  - `doctor` and Everything search;
  - the Gemini voice and the offline local-voice fallback;
  - a Claude-written talk with decks (checked in PowerPoint);
  - an internet answer via SearXNG, "what's on my screen" on the real screen, and an app launch with window check;
  - Yandex smart home on real devices: socket, bulb, wall switch, brightness, warm white and all four home routines.
- Not yet verified:
  - whether the bass setting affects the HT-S400;
  - a live "Hey JARVIS";
  - push on a real iPhone;
  - pictures from Gemini (paid tier needed) and Pexels (key needed);
  - live dictation and barge-in.

### Where things are
Code: [`developer_core/jarvis_local/`](developer_core/jarvis_local/) · Third-party components: [`NOTICE.md`](NOTICE.md) · License: [`LICENSE`](LICENSE)

