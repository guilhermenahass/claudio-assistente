"""
Telegram: o Claudio no celular.

Roda numa thread dentro do server.py. Long polling na Bot API com urllib, sem
lib extra. Mesmo cérebro (runtime), mesma memória (vault), mesma fila de
aprovação (approvals): botão Aprovar/Negar chega aqui mesmo quando o pedido
veio da tela do PC.

Só responde ao chat do Guilherme (TELEGRAM_CHAT_ID). Primeira mensagem de um
chat desconhecido é recusada e o id vai pro log, pra ele copiar pro .env.
"""
import json
import os
import pathlib
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import approvals
import commands
import memory
import runtime
import voice

TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
API = f"https://api.telegram.org/bot{TOKEN}"
FILE_API = f"https://api.telegram.org/file/bot{TOKEN}"
ROOT = pathlib.Path(__file__).resolve().parent
ANEXOS = ROOT / "anexos"
FFMPEG = voice.FFMPEG

_STATE = {"offset": 0, "started": False}
_SENT_APPROVALS = set()


def enabled():
    return bool(TOKEN)


# ── chamadas à API ──────────────────────────────────────────────
def _call(method, payload=None, files=None, timeout=40):
    url = f"{API}/{method}"
    if files:
        boundary = "----claudio" + os.urandom(8).hex()
        body = b""
        for k, v in (payload or {}).items():
            body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n").encode()
        for k, (name, data, mime) in files.items():
            body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{name}\"\r\n"
                     f"Content-Type: {mime}\r\n\r\n").encode() + data + b"\r\n"
        body += f"--{boundary}--\r\n".encode()
        req = urllib.request.Request(url, data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    else:
        req = urllib.request.Request(url, data=json.dumps(payload or {}).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def send_text(text, chat_id=None, buttons=None):
    payload = {"chat_id": chat_id or CHAT_ID, "text": text[:4000]}
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": [buttons]}
    return _call("sendMessage", payload)


def send_voice(text, chat_id=None):
    """TTS -> mp3 -> ogg/opus (formato de mensagem de voz do Telegram)."""
    if not (voice.available() and FFMPEG):
        return None
    mp3 = voice.speak(text[:2500])
    conv = subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", "pipe:0", "-c:a", "libopus", "-b:a", "48k", "-f", "ogg", "pipe:1"],
                          input=mp3, capture_output=True, timeout=60)
    if conv.returncode != 0:
        return None
    return _call("sendVoice", {"chat_id": chat_id or CHAT_ID}, files={"voice": ("claudio.ogg", conv.stdout, "audio/ogg")})


def notify(text, spoken=True):
    """Aviso proativo (fase 5 usa). Texto sempre; áudio se der."""
    if not (enabled() and CHAT_ID):
        return False
    try:
        send_text(text)
        if spoken:
            send_voice(text)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"  telegram: falha ao avisar: {e}")
        return False


def _download(file_id, name):
    info = _call("getFile", {"file_id": file_id})["result"]
    folder = ANEXOS / time.strftime("%Y-%m-%d")
    folder.mkdir(parents=True, exist_ok=True)
    ext = os.path.splitext(info["file_path"])[1]
    dest = folder / (name if name else f"telegram-{int(time.time())}{ext}")
    with urllib.request.urlopen(f"{FILE_API}/{info['file_path']}", timeout=60) as r:
        dest.write_bytes(r.read())
    return dest


# ── um turno vindo do celular ───────────────────────────────────
def _run_turn(message):
    """Executa no cérebro compartilhado. Retorna texto final."""
    cmd = commands.handle(message, runner=lambda m: runtime.run(m, None, commands.context_block(m)))
    if cmd and cmd.get("message") is None:
        return cmd.get("reply", "Feito.")
    if cmd:
        message = cmd["message"]
    system = commands.context_block(message) or None
    out, err = [], None
    for ev in runtime.run(message, runtime._BRAIN.session_id, system):
        if ev.get("t") == "delta":
            out.append(ev.get("text", ""))
        elif ev.get("t") == "error":
            err = ev.get("message")
    text = "".join(out).strip()
    return text or (f"Deu erro: {err}" if err else "Sem resposta.")


def _handle_message(msg):
    global CHAT_ID
    chat_id = str(msg["chat"]["id"])
    if not CHAT_ID:
        # Primeiro contato: aceita e grava (uma pessoa só usa este bot).
        CHAT_ID = chat_id
        _persist_chat_id(chat_id)
        send_text("Oi, Guilherme. Sou o Claudio. Chat registrado.", chat_id)
    elif chat_id != CHAT_ID:
        print(f"  telegram: chat desconhecido {chat_id} ({msg['chat'].get('first_name', '')}). Ignorado.")
        try:
            send_text("Não autorizado.", chat_id)
        except Exception:
            pass
        return

    text = (msg.get("text") or msg.get("caption") or "").strip()
    attachments = []
    try:
        if msg.get("voice") or msg.get("audio"):
            f = msg.get("voice") or msg.get("audio")
            path = _download(f["file_id"], "")
            heard = voice.transcribe(path.read_bytes(), "audio/ogg")
            if not heard:
                send_text("Não entendi o áudio.")
                return
            text = (text + "\n" + heard).strip() if text else heard
        if msg.get("photo"):
            path = _download(msg["photo"][-1]["file_id"], "")
            attachments.append(path)
        if msg.get("document"):
            d = msg["document"]
            attachments.append(_download(d["file_id"], d.get("file_name", "")))
    except Exception as e:  # noqa: BLE001
        send_text(f"Não consegui baixar o anexo: {e}")
        return

    if attachments:
        text = (text or "Olha isso.") + "\n\nAnexos:\n" + "\n".join(f"- {p}" for p in attachments)
    if not text:
        return

    _call("sendChatAction", {"chat_id": CHAT_ID, "action": "typing"})
    reply = _run_turn(text)
    send_text(reply)
    try:
        send_voice(reply)
    except Exception as e:  # noqa: BLE001
        print(f"  telegram: sem áudio: {e}")


def _safe_handle(msg):
    try:
        _handle_message(msg)
    except Exception as e:  # noqa: BLE001
        print(f"  telegram: erro no turno: {e}", flush=True)
        try:
            send_text(f"Deu erro aqui: {str(e)[:300]}", str(msg["chat"]["id"]))
        except Exception:
            pass


def _handle_callback(cb):
    data = cb.get("data", "")
    if ":" in data:
        kind, aid = data.split(":", 1)
        ok = approvals.answer(aid, kind == "ok")
        _call("answerCallbackQuery", {"callback_query_id": cb["id"], "text": "Aprovado" if kind == "ok" else "Negado"})
        try:
            m = cb.get("message") or {}
            _call("editMessageText", {"chat_id": m["chat"]["id"], "message_id": m["message_id"],
                                      "text": m.get("text", "") + ("\n\n✅ Aprovado" if kind == "ok" else "\n\n⛔ Negado")})
        except Exception:
            pass
        return ok


def _persist_chat_id(chat_id):
    env = ROOT / ".env"
    try:
        text = env.read_text(encoding="utf-8") if env.exists() else ""
        if "TELEGRAM_CHAT_ID=" in text:
            import re
            text = re.sub(r"TELEGRAM_CHAT_ID=.*", f"TELEGRAM_CHAT_ID={chat_id}", text)
        else:
            text += f"\nTELEGRAM_CHAT_ID={chat_id}\n"
        env.write_text(text, encoding="utf-8")
    except OSError as e:
        print(f"  telegram: não gravei chat id no .env: {e}")


# ── aprovações pendentes viram botões no celular ────────────────
def _push_approvals(only=None):
    if not CHAT_ID:
        return
    for req in ([only] if only else approvals.pending()):
        if req["id"] in _SENT_APPROVALS:
            continue
        _SENT_APPROVALS.add(req["id"])
        try:
            send_text("Aprovar?\n" + req["summary"][:900],
                      buttons=[{"text": "Aprovar", "callback_data": f"ok:{req['id']}"},
                               {"text": "Negar", "callback_data": f"no:{req['id']}"}])
        except Exception as e:  # noqa: BLE001
            print(f"  telegram: falha ao enviar aprovação: {e}")


# ── loop ────────────────────────────────────────────────────────
def _loop():
    print(f"  telegram     ligado" + (f" · chat {CHAT_ID}" if CHAT_ID else " · aguardando primeira mensagem pra registrar o chat"))
    while True:
        try:
            _push_approvals()
            res = _call("getUpdates", {"offset": _STATE["offset"], "timeout": 20,
                                       "allowed_updates": ["message", "callback_query"]}, timeout=30)
            for upd in res.get("result", []):
                _STATE["offset"] = upd["update_id"] + 1
                if "callback_query" in upd:
                    _handle_callback(upd["callback_query"])
                elif "message" in upd:
                    threading.Thread(target=_safe_handle, args=(upd["message"],), daemon=True).start()
        except urllib.error.HTTPError as e:
            print(f"  telegram: HTTP {e.code}: {e.read(200)!r}")
            time.sleep(5)
        except Exception as e:  # noqa: BLE001
            print(f"  telegram: {e}")
            time.sleep(3)


def start():
    if not enabled() or _STATE["started"]:
        return False
    _STATE["started"] = True
    approvals.ON_ASK.append(lambda req: _push_approvals(req))
    threading.Thread(target=_loop, daemon=True).start()
    return True


def demo():
    """Sem rede: só valida o multipart e a conversão de voz."""
    assert FFMPEG, "ffmpeg ausente"
    mp3 = voice.speak("Teste do Telegram.")
    conv = subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", "pipe:0", "-c:a", "libopus", "-f", "ogg", "pipe:1"],
                          input=mp3, capture_output=True, timeout=60)
    assert conv.returncode == 0 and conv.stdout[:4] == b"OggS", conv.stderr[:200]
    print("demo ok", len(conv.stdout), "bytes ogg")


if __name__ == "__main__":
    demo()
