"""
Agendador e avisos do Claudio.

rotinas.json: lista de rotinas. Claudio edita o arquivo quando o Guilherme pede.
  {"id": "briefing", "nome": "Briefing do dia", "hora": "07:30", "dias": ["seg","ter","qua","qui","sex"],
   "prompt": "...", "voz": true}
  {"id": "x1", "nome": "Lembrete", "quando": "2026-09-20T15:00", "prompt": "...", "voz": true}   # uma vez

silencio.json: {"ate": "2026-09-19T12:00"} -> nenhum aviso até lá (modo prova).

Aviso = Telegram (texto + voz) + áudio no PC (ffplay). Urgente = também CallMeBot, se configurado.
"""
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime

import telegram
import voice

ROOT = pathlib.Path(__file__).resolve().parent
ROTINAS = ROOT / "rotinas.json"
SILENCIO = ROOT / "silencio.json"
DIAS = ["seg", "ter", "qua", "qui", "sex", "sab", "dom"]
FFPLAY = shutil.which("ffplay") or (os.path.join(os.path.dirname(voice.FFMPEG), "ffplay.exe") if voice.FFMPEG else "")
CALLMEBOT_USER = os.environ.get("CALLMEBOT_USER", "").strip()   # @username do Telegram, depois de autorizar o bot
_LAST = {}   # id -> "YYYY-MM-DD HH:MM" da última execução


def load():
    try:
        data = json.loads(ROTINAS.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def save(items):
    ROTINAS.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def quiet_until():
    try:
        ate = json.loads(SILENCIO.read_text(encoding="utf-8")).get("ate", "")
        dt = datetime.fromisoformat(ate)
        return dt if dt > datetime.now() else None
    except (OSError, ValueError, AttributeError):
        return None


# ── avisos ──────────────────────────────────────────────────────
def play_local(text):
    if not (FFPLAY and voice.available()):
        return
    try:
        mp3 = voice.speak(text[:2500])
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(mp3)
        subprocess.Popen([FFPLAY, "-nodisp", "-autoexit", "-loglevel", "quiet", f.name],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:  # noqa: BLE001
        print(f"  aviso: sem áudio local: {e}")


def call_urgent(text):
    if not CALLMEBOT_USER:
        return False
    try:
        url = "https://api.callmebot.com/start.php?" + urllib.parse.urlencode({"user": CALLMEBOT_USER, "text": text[:300], "lang": "pt-BR-Standard-B"})
        urllib.request.urlopen(url, timeout=20).read()
        return True
    except Exception as e:  # noqa: BLE001
        print(f"  aviso: CallMeBot falhou: {e}")
        return False


def notify(text, spoken=True, urgent=False, force=False):
    """Manda por todos os canais. Respeita silêncio, salvo force."""
    q = quiet_until()
    if q and not force:
        print(f"  aviso: em silêncio até {q:%H:%M}, guardado: {text[:60]}")
        return False
    telegram.notify(text, spoken=spoken)
    if spoken:
        play_local(text)
    if urgent:
        call_urgent(text)
    return True


# ── execução ────────────────────────────────────────────────────
def _due(item, now):
    key = now.strftime("%Y-%m-%d %H:%M")
    if _LAST.get(item.get("id")) == key:
        return False
    if item.get("quando"):
        try:
            return datetime.fromisoformat(item["quando"]) <= now
        except ValueError:
            return False
    if item.get("hora") != now.strftime("%H:%M"):
        return False
    dias = item.get("dias") or DIAS
    return DIAS[now.weekday()] in dias


def run_item(item):
    now = datetime.now()
    _LAST[item.get("id")] = now.strftime("%Y-%m-%d %H:%M")
    print(f"  rotina: {item.get('nome')} ({now:%H:%M})")
    try:
        reply = telegram._run_turn(item["prompt"])
    except Exception as e:  # noqa: BLE001
        reply = f"A rotina {item.get('nome')} falhou: {e}"
    notify(reply, spoken=item.get("voz", True), urgent=item.get("urgente", False))
    if item.get("quando"):
        save([i for i in load() if i.get("id") != item.get("id")])


def _loop():
    while True:
        try:
            now = datetime.now()
            for item in load():
                if _due(item, now):
                    threading.Thread(target=run_item, args=(item,), daemon=True).start()
        except Exception as e:  # noqa: BLE001
            print(f"  agendador: {e}")
        time.sleep(30)


def start():
    threading.Thread(target=_loop, daemon=True).start()
    print(f"  agendador    {len(load())} rotinas · {'silêncio até ' + quiet_until().strftime('%d/%m %H:%M') if quiet_until() else 'ativo'}")


def demo():
    now = datetime(2026, 9, 21, 7, 30)   # segunda
    assert _due({"id": "a", "hora": "07:30", "dias": ["seg"]}, now)
    assert not _due({"id": "b", "hora": "07:30", "dias": ["ter"]}, now)
    assert _due({"id": "c", "quando": "2026-09-21T07:00"}, now)
    assert not _due({"id": "d", "quando": "2026-09-21T08:00"}, now)
    _LAST["a"] = "2026-09-21 07:30"
    assert not _due({"id": "a", "hora": "07:30", "dias": ["seg"]}, now), "não repete no mesmo minuto"
    print("demo ok")


if __name__ == "__main__":
    demo()
