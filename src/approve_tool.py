"""
Servidor MCP (stdio) que o claude usa como --permission-prompt-tool.

O claude chama a ferramenta `approve` com {tool_name, input}. Este script manda
o pedido pro server.py (/api/approval/ask), espera o Guilherme clicar Aprovar ou
Negar na tela (/api/approval/wait) e devolve a decisão no formato que o claude
espera: {"behavior":"allow","updatedInput":...} ou {"behavior":"deny","message":...}.

Roda como processo filho do claude; fala com o servidor pela porta local.
"""
import json
import os
import sys
import urllib.request

PORT = os.environ.get("CLAUDIO_PORT", "8720")
TOKEN = os.environ.get("CLAUDIO_INTERNAL_TOKEN", "")
BASE = f"http://127.0.0.1:{PORT}"
WAIT_MINUTES = int(os.environ.get("CLAUDIO_APPROVAL_MINUTES", "10"))


def _http(path, payload=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data, method="POST" if data else "GET",
                                 headers={"Content-Type": "application/json",
                                          "X-Claudio-Internal": TOKEN})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read().decode("utf-8"))


def decide(tool_name, tool_input):
    try:
        aid = _http("/api/approval/ask", {"tool": tool_name, "input": tool_input})["id"]
        for _ in range(WAIT_MINUTES * 3):          # cada wait dura até 20 s
            res = _http(f"/api/approval/wait?id={aid}")
            if res.get("decision"):
                if res["decision"] == "allow":
                    return {"behavior": "allow", "updatedInput": tool_input}
                return {"behavior": "deny", "message": res.get("message") or "negado"}
        return {"behavior": "deny", "message": "ninguém respondeu a tempo"}
    except Exception as e:  # noqa: BLE001
        return {"behavior": "deny", "message": f"aprovação indisponível: {e}"}


TOOL = {
    "name": "approve",
    "description": "Pede aprovação do Guilherme na tela do Claudio antes de executar uma ferramenta.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "tool_name": {"type": "string"},
            "input": {"type": "object"},
            "tool_use_id": {"type": "string"},
        },
        "required": ["tool_name", "input"],
    },
}


def serve():
    out = sys.stdout.buffer

    def send(msg):
        out.write((json.dumps(msg) + "\n").encode("utf-8"))
        out.flush()

    for raw in sys.stdin.buffer:
        raw = raw.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
        except json.JSONDecodeError:
            continue
        mid, method, params = req.get("id"), req.get("method"), req.get("params") or {}
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "claudio", "version": "1.0"}}})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": mid, "result": {"tools": [TOOL]}})
        elif method == "tools/call":
            args = params.get("arguments") or {}
            result = decide(args.get("tool_name", "?"), args.get("input") or {})
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": json.dumps(result)}]}})
        elif method == "ping":
            send({"jsonrpc": "2.0", "id": mid, "result": {}})
        elif mid is not None:
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"método desconhecido: {method}"}})


if __name__ == "__main__":
    serve()
