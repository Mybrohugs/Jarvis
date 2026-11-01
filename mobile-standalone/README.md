# JARVIS Mobile

Личный ассистент JARVIS для телефона (iPhone и Android), который работает **без компьютера**.
Personal JARVIS assistant for any phone that works **without the PC**.

- Мозг — Claude (ключ владельца, хранится только в телефоне); ответы из интернета через поиск Claude.
- Голос — Gemini TTS «Schedar» с эффектом робота, как у JARVIS на ПК; звук идёт потоком и начинается
  примерно через секунду, пока остальное ещё готовится. Распознавание речи — Gemini.
  Нажмите на шар, пока JARVIS говорит, — он замолчит и сразу будет слушать.
- Если голос Gemini недоступен (например, исчерпан бесплатный дневной лимит), ответ читает голос телефона,
  а причина видна на экране в скобках.
- Google Календарь: встречи, напоминания (уведомления присылает сам Google), утренний брифинг с погодой.
- Файлы между телефоном и домашним ПК, когда тот включён (через Tailscale и шлюз JARVIS Local).
- Шар из точек как на ПК-версии; удерживайте его, чтобы говорить.

Это статическая страница: ни ключей, ни данных здесь нет, всё хранится в телефоне владельца.
A static page: no keys or data in this repository; everything stays on the owner's phone.

© 2026 Roman Makhar. Использование только с письменного разрешения автора — см. [LICENSE](LICENSE).


## Установка и настройка

Разместите содержимое этой папки на HTTPS-хостинге и добавьте страницу на главный экран телефона: Safari на iPhone или Chrome на Android. Покупать домен не требуется. Для работы с Claude, голосом Gemini и Google Calendar укажите собственные данные в настройках приложения; их нельзя помещать в исходный код. Подробные шаги, настройка OAuth и подключение к домашнему ПК описаны в [руководстве по быстрому старту](../docs/QUICKSTART.md).

## Install and setup

Host the contents of this folder on HTTPS and add the page to your phone's home screen (Safari on iPhone or Chrome on Android). A custom domain is not required. Add your own provider credentials in the app settings; never commit them to source code. See the [quick-start guide](../docs/QUICKSTART.md) for OAuth and optional PC pairing details.
