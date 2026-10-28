""""Что на экране?" — a local vision model (Ollama, Qwen2.5-VL 3B) reads a screenshot and answers in
Russian: which program is open, what matters on it, the exact text of an error. The picture never
leaves the PC; the model is unloaded from video memory two minutes after the last question.
"""
from __future__ import annotations
import base64
import http.client
import io
import json
from .session import Denied

MODEL = 'qwen2.5vl:3b'
PROMPT = ('Это снимок экрана компьютера пользователя. Ответь по-русски, 2–4 коротких предложения: какая программа '
          'или сайт открыты и что на экране главное. Если видна ошибка или предупреждение, процитируй её текст дословно. '
          'Не выдумывай того, чего не видно.')


def ollama_chat(model, prompt, image_b64, timeout=120):
    conn = http.client.HTTPConnection('127.0.0.1', 11434, timeout=timeout)
    try:
        conn.request('POST', '/api/chat', body=json.dumps({
            'model': model, 'stream': False, 'keep_alive': '2m', 'options': {'num_ctx': 4096, 'temperature': 0.2},
            'messages': [{'role': 'user', 'content': prompt, 'images': [image_b64]}]}),
            headers={'Content-Type': 'application/json'})
        r = conn.getresponse()
        raw = r.read(2_000_000)
        if r.status == 404:
            raise Denied('Модель %s не установлена: ollama pull %s' % (model, model))
        if r.status != 200:
            raise Denied('Ollama HTTP %d' % r.status)
        return (json.loads(raw).get('message') or {}).get('content', '').strip()
    except (OSError, ValueError, http.client.HTTPException) as exc:
        raise Denied('Ollama unreachable: ' + type(exc).__name__) from None
    finally:
        conn.close()


def png_of(img, max_side=1280):
    img = img.copy()
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.convert('RGB').save(buf, 'PNG', optimize=True)
    return buf.getvalue()


def describe(png, question='', chat=ollama_chat, model=MODEL):
    prompt = PROMPT + (' Вопрос пользователя: ' + question.strip()[:300] if question and question.strip() else '')
    text = chat(model, prompt, base64.b64encode(png).decode())
    if not text:
        raise Denied('Модель не ответила')
    return text[:1200]
