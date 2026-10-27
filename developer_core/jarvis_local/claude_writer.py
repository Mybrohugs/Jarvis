"""Claude API for JARVIS writing tasks: conference speech text + slide script as strict JSON.

The Anthropic API key is stored with Windows user DPAPI in <DEV>/.claude-dev and never printed.
Only the topic and the user's own notes are sent to Anthropic. The model is told not to invent
facts or statistics: unknown numbers come back as "[уточнить]" placeholders for the user to fill.
  python -m developer_core.jarvis_local.claude_writer set-key --from-clipboard
"""
from __future__ import annotations
import argparse
import json
import sys
from .gemini_voice import DEV_ROOT, KeyStore, take_clipboard
from .session import Denied

ROOT = DEV_ROOT / '.claude-dev'
MODEL = 'claude-opus-5-5'
FALLBACK_BETA = 'server-side-fallback-2026-07-01'
WORDS_PER_MIN = 130  # calm conference pace in Russian
KINDS = ['title', 'section', 'statement', 'bullets', 'number', 'quote', 'compare', 'closing']

_STR = {'type': 'string'}
_LIST = {'type': 'array', 'items': _STR}
SLIDE = {'type': 'object', 'additionalProperties': False,
         'properties': {'kind': {'type': 'string', 'enum': KINDS}, 'title': _STR, 'subtitle': _STR,
                        'bullets': _LIST, 'number': _STR, 'number_label': _STR, 'quote': _STR,
                        'author': _STR, 'left_title': _STR, 'left': _LIST, 'right_title': _STR,
                        'right': _LIST, 'notes': _STR, 'image': _STR},
         'required': ['kind', 'title', 'subtitle', 'bullets', 'number', 'number_label', 'quote', 'author',
                      'left_title', 'left', 'right_title', 'right', 'notes', 'image']}
SECTION = {'type': 'object', 'additionalProperties': False,
           'properties': {'heading': _STR, 'text': _STR}, 'required': ['heading', 'text']}
SCHEMA = {'type': 'object', 'additionalProperties': False,
          'properties': {'title': _STR, 'subtitle': _STR, 'speech': {'type': 'array', 'items': SECTION},
                         'slides': {'type': 'array', 'items': SLIDE}, 'to_verify': _LIST},
          'required': ['title', 'subtitle', 'speech', 'slides', 'to_verify']}

SYSTEM = """Ты — сильный спичрайтер и дизайнер презентаций для конференций. Пишешь по-русски, живым устным языком: \
короткие фразы, ритм, паузы, обращение к залу, без канцелярита.

Текст выступления:
- объём под заданную длительность, примерно %(wpm)d слов в минуту;
- открытие с крючком (вопрос, неожиданный факт из материалов пользователя, короткая история), 3–5 смысловых блоков, \
одна сквозная история или образ, сильная концовка с призывом или мыслью, которую унесут с собой;
- разделы speech идут в порядке произнесения, heading — короткое рабочее название блока.

Слайды:
- одна мысль на слайд; заголовок до 7 слов; не больше 4 пунктов по 3–8 слов; никаких абзацев на слайде;
- чередуй виды: title (первый), section, statement (одна крупная фраза в title), bullets, number (одна крупная цифра \
в number + пояснение в number_label), quote (quote + author), compare (left_title/left против right_title/right), \
closing (последний);
- notes — что именно говорить на этом слайде, дословный фрагмент текста выступления;
- неиспользуемые поля оставляй пустыми ("" или []).

Картинки (поле image): примерно на трети слайдов, только там, где картинка усиливает смысл — title, section, statement, bullets, closing; на number, quote и compare не ставь. image — по-английски, 6–15 слов, конкретная сцена или метафора, которую легко найти на фотостоке или нарисовать: кто/что, где, настроение (например, "night city skyline seen from a quiet home office, warm desk lamp"). Без текста, логотипов и узнаваемых людей. Где картинка не нужна — "".

Честность: не выдумывай статистику, исследования, цитаты и имена. Используй только факты из материалов пользователя \
и общеизвестные. Если для слайда нужна цифра или факт, которого нет, ставь "[уточнить: что именно]" и добавь пункт в \
to_verify. Цитату без надёжного источника не используй, вместо неё дай мысль от лица докладчика.""" % {'wpm': WORDS_PER_MIN}


def keys():
    return KeyStore(ROOT, name='anthropic-key', label='JARVIS Claude DEV', service='Claude')


def brief(topic, minutes, audience='', tone='', notes=''):
    words = minutes * WORDS_PER_MIN
    lines = ['Тема выступления: ' + topic.strip(),
             'Длительность: %d мин. Объём текста speech: %d–%d слов — это обязательно, текст читается вслух '
             'ровно столько минут; короткий текст оставит докладчика без материала.' % (minutes, words, int(words * 1.1))]
    if audience:
        lines.append('Аудитория: ' + audience.strip())
    if tone:
        lines.append('Тон: ' + tone.strip())
    if notes:
        lines.append('Материалы и факты докладчика (используй их, других цифр не придумывай):\n' + notes.strip())
    lines.append('Слайдов: примерно %d.' % max(6, min(20, round(minutes * 1.2))))
    return '\n'.join(lines)


def validate_plan(plan):
    if not plan.get('speech') or not plan.get('slides'):
        raise Denied('Claude returned an empty plan')
    if any(s.get('kind') not in KINDS for s in plan['slides']):
        raise Denied('Unknown slide kind')
    plan['words'] = sum(len(s['text'].split()) for s in plan['speech'])
    plan['minutes_estimate'] = round(plan['words'] / WORDS_PER_MIN, 1)
    return plan


def speech_plan(topic, minutes=10, audience='', tone='', notes='', client=None, effort='high'):
    """One Claude call -> validated plan dict (speech sections + slides + facts to verify)."""
    if not topic.strip() or not 1 <= int(minutes) <= 60:
        raise Denied('Topic is required and the duration must be 1..60 minutes')
    import anthropic
    client = client or anthropic.Anthropic(api_key=keys().load())
    try:
        with client.beta.messages.stream(
                model=MODEL, max_tokens=64000, system=SYSTEM, betas=[FALLBACK_BETA], fallbacks='default',
                output_config={'effort': effort, 'format': {'type': 'json_schema', 'schema': SCHEMA}},
                messages=[{'role': 'user', 'content': brief(topic, int(minutes), audience, tone, notes)}]) as stream:
            message = stream.get_final_message()
    except anthropic.AuthenticationError:
        raise Denied('Claude rejected the API key') from None
    except anthropic.RateLimitError:
        raise Denied('Claude rate limit, try again in a minute') from None
    except anthropic.APIStatusError as exc:
        raise Denied('Claude API error %d' % exc.status_code) from None
    except anthropic.APIConnectionError:
        raise Denied('Claude unreachable: check the internet connection') from None
    if message.stop_reason == 'refusal':
        raise Denied('Claude declined this topic')
    if message.stop_reason == 'max_tokens':
        raise Denied('Claude output was cut off; try a shorter duration')
    text = next((b.text for b in message.content if b.type == 'text'), '')
    plan = validate_plan(json.loads(text))
    plan['usage'] = {'model': message.model, 'input_tokens': message.usage.input_tokens,
                     'output_tokens': message.usage.output_tokens}
    return plan


def main(argv=None):
    ap = argparse.ArgumentParser(description='Claude writer for JARVIS')
    sub = ap.add_subparsers(dest='cmd', required=True)
    k = sub.add_parser('set-key')
    k.add_argument('--from-clipboard', action='store_true')
    a = ap.parse_args(argv)
    if a.cmd == 'set-key':
        import getpass
        text = take_clipboard() if a.from_clipboard else getpass.getpass('Claude API key (input hidden): ')
        print(json.dumps(keys().save(text)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
