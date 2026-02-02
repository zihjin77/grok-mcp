# mcp_server.py (Flask MCP JSON-RPC wrapper for grok_search.py)
import json
import os
import subprocess
import sys
from pathlib import Path

from flask import Flask, jsonify, request

app = Flask(__name__)

ROOT_DIR = Path(__file__).resolve().parent
GROK_SCRIPT_PATH = ROOT_DIR / "scripts" / "grok_search.py"

SERVER_INFO = {"name": "grok-search-mcp", "version": "0.1.0"}

TOOL_NAME = "grok_search"

TOOL_DEF = {
    "name": TOOL_NAME,
    "description": "Search the web or X using Grok's real-time search capability.",
    # MCP uses inputSchema (JSON Schema), not `parameters`
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query / research task."}
        },
        "required": ["query"],
        "additionalProperties": False,
    },
}

def jsonrpc_result(_id, result):
    return jsonify({"jsonrpc": "2.0", "id": _id, "result": result})

def jsonrpc_error(_id, code, message, data=None):
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return jsonify({"jsonrpc": "2.0", "id": _id, "error": err})

def run_grok_search(query: str) -> dict:
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    cmd = [sys.executable, str(GROK_SCRIPT_PATH), "--query", query]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=120,
        )
    except subprocess.TimeoutExpired as exc:
        return {"ok": False, "error": "timeout", "detail": str(exc)}
    except FileNotFoundError as exc:
        return {
            "ok": False,
            "error": "script_not_found",
            "detail": str(exc),
            "path": str(GROK_SCRIPT_PATH),
        }
    except Exception as exc:
        return {"ok": False, "error": "subprocess_error", "detail": str(exc)}

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()

    if stdout:
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            return {
                "ok": False,
                "error": "non_json_output",
                "raw_stdout": stdout,
                "raw_stderr": stderr,
                "returncode": proc.returncode,
            }

    return {
        "ok": False,
        "error": "empty_output",
        "returncode": proc.returncode,
        "raw_stderr": stderr,
    }

def handle_rpc(msg: dict):
    # Notification: no id => no response
    _id = msg.get("id", None)
    method = msg.get("method", "")
    params = msg.get("params") or {}

    # Basic validation
    if msg.get("jsonrpc") != "2.0" or not isinstance(method, str) or not method:
        return jsonrpc_error(_id, -32600, "Invalid Request", data=msg)

    # ---- MCP methods ----
    if method == "initialize":
        # echo back protocolVersion when provided
        protocol_version = params.get("protocolVersion") or "2024-11-05"
        result = {
            "protocolVersion": protocol_version,
            "serverInfo": SERVER_INFO,
            "capabilities": {
                "tools": {},   # declare tools capability
            },
        }
        return jsonrpc_result(_id, result)

    if method == "tools/list":
        return jsonrpc_result(_id, {"tools": [TOOL_DEF]})

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments")

        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                return jsonrpc_error(
                    _id,
                    -32602,
                    "Invalid arguments: string payload is not valid JSON.",
                    data={"arguments": arguments},
                )

        if arguments is None:
            arguments = {}

        if not isinstance(arguments, dict):
            return jsonrpc_error(
                _id,
                -32602,
                "Invalid arguments: expected object.",
                data={"arguments_type": type(arguments).__name__},
            )

        if name != TOOL_NAME:
            return jsonrpc_error(_id, -32602, f"Unknown tool: {name}")

        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            return jsonrpc_error(_id, -32602, "Missing required argument: query")

        out = run_grok_search(query.strip())

        # MCP tool result: content array
        return jsonrpc_result(
            _id,
            {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(out, ensure_ascii=False, indent=2),
                    }
                ],
                "isError": (not bool(out.get("ok", True))),
            },
        )

    # Some clients send notifications/initialized
    if method == "notifications/initialized":
        return jsonrpc_result(_id, {})

    # Unknown method
    return jsonrpc_error(_id, -32601, f"Method not found: {method}")

@app.route("/", methods=["GET"])
def health():
    return jsonify({"ok": True, "server": SERVER_INFO})


@app.route("/", methods=["POST"])
@app.route("/mcp", methods=["POST"])
@app.route("/mcp/", methods=["POST"])
def rpc_entry():
    payload = request.get_json(silent=True)
    print("RPC payload:", payload, flush=True)

    if isinstance(payload, list):
        responses = []
        for msg in payload:
            if isinstance(msg, dict):
                resp = handle_rpc(msg)
                if isinstance(resp, tuple):
                    continue
                responses.append(resp.get_json())
        resp_payload = jsonify(responses)
        print("RPC response:", responses, flush=True)
        return resp_payload

    if not isinstance(payload, dict):
        resp = jsonrpc_error(None, -32600, "Invalid Request", data=payload)
        print("RPC response:", resp.get_json(), flush=True)
        return resp

    resp = handle_rpc(payload)
    if isinstance(resp, tuple):
        print("RPC response: tuple", resp, flush=True)
        return resp
    print("RPC response:", resp.get_json(), flush=True)
    return resp

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5678, debug=True)
