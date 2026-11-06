# -*- coding: utf-8 -*-
"""Official Sign in with ChatGPT plan-usage bridge for a LOCAL Windows JARVIS.

Does not use cookies, chatgpt.com browser scraping, or ChatGPT backend endpoints.
Does not support audio/Realtme. A user must explicitly authorize the account once.
Windows-only credential protection via DPAPI. All AI tasks use plan-usage OAuth.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

AUTH_BASE = "https://auth.openai.com"
AUTH_URL = AUTH_BASE + "/api/accounts/authorize"
TOKEN_URL = AUTH_BASE + "/api/accounts/oauth/token"
RESOURCE = "https://api.openai.com/v1"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
APP_LABEL = "JARVIS Local AI Assistant"
ROOT = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")) / "JarvisChatGPTPlan"
HOST_FILE = ROOT / "host.json"
SECRET_FILE = ROOT / "plan_oauth.dpapi"
MAX_PROMPT = 12000


def _json_request(url: str, method: str = "GET", body: dict | None = None,
                  bearer: str | None = None, timeout: int = 30) -> dict:
    headers = {"Accept": "application/json", "User-Agent": "JARVIS-Local-Bridge/1.0"}
    data = None
    if body is not None:
        data = urllib.parse.urlencode(body).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if bearer:
        headers["Authorization"] = "Bearer " + bearer
    req = urllib.request.Request(url, method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read(1024 * 1024).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # Avoid output of request headers/credentials/authorization callbacks.
        raise RuntimeError(f"HTTP {exc.code} ({urllib.parse.urlsplit(url).hostname})") from None


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _verify_id_token(token: str, client_id: str, nonce: str) -> dict:
    """Verify OpenAI OIDC ID token, including signed JWKS, audience and nonce."""
    from cryptography.hazmat.primitives.asymmetric import rsa, padding
    from cryptography.hazmat.primitives import hashes

    parts = token.split(".")
    if len(parts) != 3:
        raise RuntimeError("Некорректный ID token")
    head = json.loads(_b64url_decode(parts[0]))
    data = json.loads(_b64url_decode(parts[1]))
    if head.get("alg") != "RS256" or not isinstance(head.get("kid"), str):
        raise RuntimeError("Недопустимый алгоритм подписи ID token")
    cfg = _json_request(AUTH_BASE + "/.well-known/openid-configuration")
    jwks_uri = cfg.get("jwks_uri", "")
    if not jwks_uri.startswith(AUTH_BASE + "/"):
        raise RuntimeError("Недостоверный JWKS URL")
    jwks = _json_request(jwks_uri)
    matches = [k for k in jwks.get("keys", []) if k.get("kid") == head["kid"] and k.get("kty") == "RSA"]
    if len(matches) != 1:
        raise RuntimeError("Не найден доверенный ключ подписи")
    key = matches[0]
    pub = rsa.RSAPublicNumbers(
        e=int.from_bytes(_b64url_decode(key["e"]), "big"),
        n=int.from_bytes(_b64url_decode(key["n"]), "big"),
    ).public_key()
    pub.verify(_b64url_decode(parts[2]), (parts[0] + "." + parts[1]).encode("ascii"),
               padding.PKCS1v15(), hashes.SHA256())
    now = int(time.time())
    if cfg.get("issuer") != AUTH_BASE or data.get("iss") != AUTH_BASE:
        raise RuntimeError("Недостоверный issuer")
    audience = data.get("aud")
    if client_id not in (audience if isinstance(audience, list) else [audience]):
        raise RuntimeError("ID token не выдан этому приложению")
    if data.get("nonce") != nonce:
        raise RuntimeError("Nonce не совпадает")
    if int(data.get("exp", 0)) <= now or int(data.get("iat", 0)) > now + 120:
        raise RuntimeError("ID token недействителен по времени")
    if not isinstance(data.get("sub"), str) or not data["sub"]:
        raise RuntimeError("В ID token нет sub")
    return {"subject": data["sub"], "email": str(data.get("email") or "")}


def _dpapi_encrypt(data: bytes) -> bytes:
    if os.name != "nt":
        raise RuntimeError("Хранение OAuth доступно только на Windows (DPAPI)")
    import win32crypt
    # Data is bound to the current Windows user account, not the Python project.
    return win32crypt.CryptProtectData(data, "JARVIS ChatGPT plan", None, None, None, 0)


def _dpapi_decrypt(data: bytes) -> bytes:
    if os.name != "nt":
        raise RuntimeError("DPAPI доступен только в Windows")
    import win32crypt
    return win32crypt.CryptUnprotectData(data, None, None, None, 0)[1]


def _atomic_write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp-" + secrets.token_hex(8))
    try:
        with open(temp, "xb") as h:
            h.write(data)
            h.flush()
            os.fsync(h.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def _load_host() -> str:
    if HOST_FILE.exists():
        obj = json.loads(HOST_FILE.read_text(encoding="utf-8"))
        host = obj.get("ext_agent_host_id")
        if isinstance(host, str) and host.startswith("urn:uuid:"):
            return host
        raise RuntimeError("Неверный host ID, ручное исправление необходимо")
    host = "urn:uuid:" + str(uuid.uuid4())
    _atomic_write(HOST_FILE, json.dumps({"ext_agent_host_id": host}).encode("utf-8"))
    return host


def _load_credential() -> dict | None:
    if not SECRET_FILE.exists():
        return None
    return json.loads(_dpapi_decrypt(SECRET_FILE.read_bytes()).decode("utf-8"))


def _save_credential(obj: dict):
    _atomic_write(SECRET_FILE, _dpapi_encrypt(json.dumps(obj, ensure_ascii=False).encode("utf-8")))


def connect(timeout_sec: int = 240) -> str:
    """Only user-initiated authorization. No automatic UI/cookie scraping."""
    old = _load_credential()
    issued = old.get("client_id") if old else None
    host_id = _load_host()
    pending_state = secrets.token_urlsafe(32)
    pending_nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    result: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urllib.parse.urlsplit(self.path)
            if url.path != "/auth/callback":
                self.send_error(404)
                return
            q = urllib.parse.parse_qs(url.query, strict_parsing=False)
            if q.get("state", [""])[0] != pending_state:
                result["error"] = "Некорректное состояние авторизации"
                self.send_error(400)
                return
            result["values"] = {k: v[0] for k, v in q.items() if v}
            page = ("<html><meta charset='utf-8'><body style='font:18px sans-serif'>"
                    "Авторизация завершена. Вернитесь в PowerShell.</body></html>")
            body = page.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            return  # Never log auth code, callback query, credentials.

    server = HTTPServer(("127.0.0.1", 0), Handler)
    server.timeout = 1
    callback = f"http://127.0.0.1:{server.server_port}/auth/callback"
    params = {
        "client_id": issued or "dynamic_agent_client",
        "ext_agent_host_id": host_id,
        "response_type": "code",
        "redirect_uri": callback,
        "scope": SCOPES,
        "resource": RESOURCE,
        "state": pending_state,
        "nonce": pending_nonce,
        "code_challenge_method": "S256",
        "code_challenge": challenge,
    }
    if not issued:
        params["agent_name_hint"] = APP_LABEL
    elif old.get("id_token"):
        params["id_token_hint"] = old["id_token"]
    url = AUTH_URL + "?" + urllib.parse.urlencode(params)
    print("Открываю официальный вход ChatGPT. Разрешите использование плана для JARVIS.")
    if not webbrowser.open(url):
        raise RuntimeError("Системный браузер не открылся. Настройте браузер по умолчанию и повторите --connect")
    deadline = time.monotonic() + timeout_sec
    try:
        while "values" not in result and "error" not in result and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    if "error" in result:
        raise RuntimeError(result["error"])
    if "values" not in result:
        raise RuntimeError("Время ожидания входа истекло; повторите --connect")
    values = result["values"]
    if values.get("error"):
        raise RuntimeError("Вход отменён или разрешение не предоставлено")
    code = values.get("code")
    client_id = issued or values.get("client_id")
    if not code or not client_id or client_id == "dynamic_agent_client":
        raise RuntimeError("Ответ OAuth не содержит code или выданный client_id")
    if issued and values.get("client_id") not in (None, issued):
        raise RuntimeError("Неожиданная смена client_id")
    tokens = _json_request(TOKEN_URL, method="POST", body={
        "grant_type": "authorization_code",
        "client_id": client_id,
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": callback,
        "resource": RESOURCE,
    })
    claims = _verify_id_token(str(tokens.get("id_token", "")), client_id, pending_nonce)
    if old and claims["subject"] != old.get("subject"):
        raise RuntimeError("Вы выбрали другой аккаунт ChatGPT. Отключите текущий отдельно.")
    scopes = str(tokens.get("scope") or "").split()
    if not {"chatgpt.tokens.use.direct", "resource.invoke"}.issubset(scopes):
        raise RuntimeError("Нет разрешения ChatGPT plan usage. Существующее подключение не изменено.")
    if not all(tokens.get(k) for k in ("access_token", "refresh_token", "id_token")):
        raise RuntimeError("OAuth не вернул необходимые токены")
    account = {
        "client_id": client_id,
        "ext_agent_host_id": host_id,
        "subject": claims["subject"],
        "email": claims["email"],
        "id_token": tokens["id_token"],
        "access_token": tokens["access_token"],
        "refresh_token": tokens["refresh_token"],
        "scope": " ".join(scopes),
        "expires_at": time.time() + int(tokens.get("expires_in") or 3600),
        "earliest_refresh_at": tokens.get("earliest_refresh_at"),
    }
    _save_credential(account)
    return "Подключение ChatGPT plan usage сохранено для текущего пользователя Windows."


_refresh_lock = threading.Lock()

def _token() -> str:
    with _refresh_lock:
        account = _load_credential()
        if account is None:
            raise RuntimeError("ChatGPT пока не подключён: выполните --connect один раз")
        if time.time() + 180 < float(account.get("expires_at", 0)):
            return account["access_token"]
        if not account.get("refresh_token"):
            raise RuntimeError("Сеанс истёк. Требуется повторный --connect")
        tokens = _json_request(TOKEN_URL, method="POST", body={
            "grant_type": "refresh_token",
            "client_id": account["client_id"],
            "refresh_token": account["refresh_token"],
            "resource": RESOURCE,
        })
        if not tokens.get("refresh_token") or not tokens.get("access_token"):
            raise RuntimeError("Не удалось обновить сеанс без повторной авторизации")
        account.update({
            "refresh_token": tokens["refresh_token"],
            "access_token": tokens["access_token"],
            "expires_at": time.time() + int(tokens.get("expires_in") or 3600),
            "scope": tokens.get("scope") or account["scope"],
        })
        _save_credential(account)
        return account["access_token"]


def _model(token: str) -> str:
    catalog = _json_request(RESOURCE + "/models", bearer=token)
    choices = [m.get("slug") for m in catalog.get("models", [])
               if m.get("visibility") == "list" and isinstance(m.get("slug"), str)]
    if not choices:
        raise RuntimeError("Нет моделей ChatGPT plan usage, доступных этому аккаунту")
    return choices[0]  # API ordering; no invented model identifier.


def _response(prompt: str, token: str, model: str) -> str:
    body = json.dumps({
        "model": model,
        "instructions": ("Ты JARVIS, помощник пользователя. Отвечай по-русски, точно и по существу. "
                         "Не утверждай, что изменил Windows, файлы или аккаунты: "
                         "эта часть работает только с текстом. При нехватке данных уточни."),
        "input": [{"role": "user", "content": prompt}],
        "store": False,
        "stream": True,
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(RESOURCE + "/responses", data=body, method="POST", headers={
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "User-Agent": "JARVIS-Local-Bridge/1.0",
    })
    completed = False
    pieces = []
    total_len = 0
    try:
        with urllib.request.urlopen(req, timeout=120) as res:
            for raw in res:
                if not raw.startswith(b"data: "):
                    continue
                line = raw[6:].strip()
                if not line or line == b"[DONE]":
                    continue
                evt = json.loads(line.decode("utf-8"))
                kind = evt.get("type")
                if kind == "response.output_text.delta":
                    d = str(evt.get("delta", ""))
                    pieces.append(d)
                    total_len += len(d)
                    if total_len > 24000:
                        raise RuntimeError("Ответ слишком длинный для голосового интерфейса")
                elif kind == "response.failed":
                    err = (evt.get("response") or {}).get("error") or {}
                    raise RuntimeError("Ответ OpenAI завершился ошибкой: " + str(err.get("code") or "unknown"))
                elif kind == "response.completed":
                    completed = True
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise RuntimeError("Достигнут лимит ChatGPT plan usage; переход к Gemini") from None
        if exc.code in (401, 403):
            raise RuntimeError("Доступ ChatGPT не подтверждён; переход к Gemini") from None
        raise RuntimeError(f"Ошибка сервиса OpenAI HTTP {exc.code}; переход к Gemini") from None
    if not completed:
        raise RuntimeError("GPT завершил поток без подтверждения результата")
    return "".join(pieces).strip() or "GPT не вернул текстовый ответ"


def dispatch(params: dict) -> str:
    operation = str(params.get("operation") or "ask").lower()
    if operation == "status":
        return status()
    if operation != "ask":
        return "Неизвестная операция: доступны ask и status"
    prompt = str(params.get("prompt") or "").strip()
    if not prompt or len(prompt) > MAX_PROMPT:
        return f"Запрос должен содержать от 1 до {MAX_PROMPT} символов"
    try:
        token = _token()
        selected = _model(token)
        answer = _response(prompt, token, selected)
        return "[ОТВЕТ GPT через официальное использование подписки ChatGPT] " + answer
    except Exception as exc:
        # No paid API fallback. JARVIS Gemini Live can answer instead.
        return f"[GPT НЕДОСТУПЕН: {type(exc).__name__}: {str(exc)[:180]}] Продолжи ответ через Gemini, если это возможно, и не утверждай, что ответ получен от GPT."


def status() -> str:
    data = _load_credential()
    if not data:
        return "ChatGPT не подключён. Один раз выполните --connect. Gemini продолжает работать."
    # Do not display the email or any token in plugin output.
    return "ChatGPT подключён через официальный Sign in with ChatGPT. Токены хранятся локально с DPAPI. GPT доступен для поддерживаемых текстовых задач, не для Realtime-аудио."


def main():
    parser = argparse.ArgumentParser(description="JARVIS official ChatGPT plan bridge")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--connect", action="store_true")
    group.add_argument("--status", action="store_true")
    group.add_argument("--ask", metavar="TEXT")
    group.add_argument("--disconnect", action="store_true")
    args = parser.parse_args()
    if args.connect:
        print(connect())
    elif args.status:
        print(status())
    elif args.ask is not None:
        answer = dispatch({"operation": "ask", "prompt": args.ask})
        print(answer)
        if answer.startswith("[GPT НЕДОСТУПЕН"):
            sys.exit(2)
    else:
        # Stop use immediately and delete local tokens; remote revoke should be done in ChatGPT Settings.
        if SECRET_FILE.exists():
            SECRET_FILE.unlink()
        print("Локальный сеанс отключён. Для полного отзыва разрешения используйте ChatGPT Settings.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("Подключение ChatGPT: " + type(exc).__name__ + ": " + str(exc)[:200], file=sys.stderr)
        sys.exit(1)
