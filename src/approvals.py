"""
Fila de aprovações: o claude pede permissão (via approve_tool.py), a tela responde.

Compartilhada entre server.py (endpoints) e runtime.py (eventos pro navegador).
"""
import threading
import time
import uuid

_LOCK = threading.Lock()
_PENDING = {}   # id -> dict(tool, input, summary, decision, message, announced, at)
ON_ASK = []     # callbacks(req) chamados na hora que um pedido nasce (Telegram usa)


def summarize(tool, inp):
    """Uma linha legível do que o claude quer fazer."""
    inp = inp or {}
    if tool in ("Bash", "PowerShell"):
        return f"{tool}: {inp.get('command', '')}"
    for key in ("file_path", "path", "url", "query", "to", "subject"):
        if key in inp:
            return f"{tool}: {inp[key]}"
    text = ", ".join(f"{k}={str(v)[:60]}" for k, v in list(inp.items())[:4])
    return f"{tool}: {text}" if text else tool


def ask(tool, inp):
    aid = uuid.uuid4().hex[:8]
    with _LOCK:
        _PENDING[aid] = dict(id=aid, tool=tool, input=inp, summary=summarize(tool, inp),
                             decision=None, message="", announced=False, at=time.time())
    for cb in ON_ASK:
        threading.Thread(target=cb, args=(dict(id=aid, tool=tool, summary=summarize(tool, inp)),), daemon=True).start()
    return aid


def wait(aid, timeout=20.0):
    """Bloqueia até decisão ou timeout. Retorna dict ou None (ainda pendente)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with _LOCK:
            item = _PENDING.get(aid)
            if item is None:
                return dict(decision="deny", message="pedido expirou")
            if item["decision"]:
                _PENDING.pop(aid, None)
                return dict(decision=item["decision"], message=item["message"])
        time.sleep(0.15)
    return None


def answer(aid, allow, message=""):
    with _LOCK:
        item = _PENDING.get(aid)
        if not item:
            return False
        item["decision"] = "allow" if allow else "deny"
        item["message"] = message or ("aprovado pelo Guilherme" if allow else "negado pelo Guilherme")
        return True


def pending():
    with _LOCK:
        return [dict(id=v["id"], tool=v["tool"], summary=v["summary"])
                for v in _PENDING.values() if not v["decision"]]


def take_unannounced():
    """Pedidos novos que a tela ainda não viu (runtime emite uma vez cada)."""
    with _LOCK:
        out = []
        for v in _PENDING.values():
            if not v["announced"] and not v["decision"]:
                v["announced"] = True
                out.append(dict(id=v["id"], tool=v["tool"], summary=v["summary"]))
        return out


def demo():
    aid = ask("Bash", {"command": "rm -rf x"})
    assert pending()[0]["summary"] == "Bash: rm -rf x"
    assert take_unannounced() and not take_unannounced()
    assert wait(aid, 0.2) is None
    assert answer(aid, True)
    assert wait(aid, 1)["decision"] == "allow"
    assert not pending()
    print("demo ok")


if __name__ == "__main__":
    demo()
