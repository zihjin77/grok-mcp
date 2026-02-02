#!/usr/bin/env python3
"""Invoke Grok's web-search endpoint and emit JSON results for MCP."""

from __future__ import annotations

import argparse
import json
import os
import sys
import io
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

try:
    import requests
except ImportError:  # pragma: no cover - emit JSON error for MCP consumers
    print(
        json.dumps(
            {
                "ok": False,
                "error": "missing_dependency",
                "detail": "The 'requests' package is required. Install with 'pip install requests'.",
            },
            ensure_ascii=False,
        )
    )
    sys.exit(1)

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = ROOT_DIR / "config.json"
DEFAULT_SYSTEM_PROMPT = (
    "You are Grok Search, an evidence-focused AI researcher. "
    "Run the provider's live search when available, synthesize the answer, "
    "and list the best sources in plain text. Always keep answers concise."
)


def configure_stdio() -> None:
    """Force stdout/stderr to UTF-8 so Grok responses with emoji won't crash on Windows."""
    target_encoding = os.environ.get("PYTHONIOENCODING") or "utf-8"
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding=target_encoding)  # type: ignore[attr-defined]
            continue
        except Exception:
            pass

        buffer = getattr(stream, "buffer", None)
        if buffer is None:
            continue
        try:
            wrapped = io.TextIOWrapper(buffer, encoding=target_encoding)
        except Exception:
            continue
        setattr(sys, name, wrapped)


configure_stdio()


class GrokSearchCliError(Exception):
    """Custom exception that carries a JSON-serialisable payload."""

    def __init__(self, payload: Dict[str, Any], exit_code: int = 1) -> None:
        super().__init__(payload.get("error") or "grok_search_cli_error")
        self.payload = payload
        self.exit_code = exit_code


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Call Grok's chat completion endpoint and output JSON for MCP.",
    )
    parser.add_argument("--query", "-q", required=True, help="Search query / research instruction.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Config file path.")
    parser.add_argument("--base-url", dest="base_url", help="Override Grok base URL.")
    parser.add_argument("--api-key", dest="api_key", help="Override Grok API key.")
    parser.add_argument("--model", help="Override model name.")
    parser.add_argument("--timeout", type=float, help="HTTP timeout in seconds.")
    parser.add_argument("--extra-body-json", dest="extra_body_json", help="Additional JSON body fields.")
    parser.add_argument("--extra-headers-json", dest="extra_headers_json", help="Additional HTTP headers.")
    parser.add_argument(
        "--system-prompt",
        dest="system_prompt",
        help="Custom system prompt. Defaults to an evidence-focused instruction.",
    )
    return parser.parse_args()


def execute() -> Dict[str, Any]:
    args = parse_args()
    settings = resolve_settings(args)
    response = call_grok(
        query=args.query,
        base_url=settings["base_url"],
        api_key=settings["api_key"],
        model=settings["model"],
        timeout=settings["timeout_seconds"],
        system_prompt=settings["system_prompt"],
        extra_body=settings["extra_body"],
        extra_headers=settings["extra_headers"],
    )
    return response


def resolve_settings(args: argparse.Namespace) -> Dict[str, Any]:
    config_path = Path(args.config).expanduser()
    config = load_json_file(config_path)

    # Optional config.local.json for secrets
    local_config_path = config_path.with_name("config.local.json")
    if local_config_path.exists():
        config.update(load_json_file(local_config_path))

    # Environment overrides
    env_map = {
        "base_url": os.getenv("GROK_BASE_URL"),
        "api_key": os.getenv("GROK_API_KEY"),
        "model": os.getenv("GROK_MODEL"),
        "system_prompt": os.getenv("GROK_SYSTEM_PROMPT"),
    }
    for key, value in env_map.items():
        if value:
            config[key] = value

    env_timeout = os.getenv("GROK_TIMEOUT_SECONDS")
    if env_timeout:
        try:
            config["timeout_seconds"] = float(env_timeout)
        except ValueError as exc:
            raise GrokSearchCliError(
                {
                    "ok": False,
                    "error": "invalid_env_timeout",
                    "detail": f"GROK_TIMEOUT_SECONDS must be numeric, got: {env_timeout}",
                }
            ) from exc

    if os.getenv("GROK_EXTRA_BODY_JSON"):
        config["extra_body"] = merge_mappings(
            config.get("extra_body"),
            parse_json_mapping(os.getenv("GROK_EXTRA_BODY_JSON"), "GROK_EXTRA_BODY_JSON"),
        )

    if os.getenv("GROK_EXTRA_HEADERS_JSON"):
        config["extra_headers"] = merge_mappings(
            config.get("extra_headers"),
            parse_json_mapping(os.getenv("GROK_EXTRA_HEADERS_JSON"), "GROK_EXTRA_HEADERS_JSON"),
        )

    # CLI overrides
    if args.base_url:
        config["base_url"] = args.base_url
    if args.api_key:
        config["api_key"] = args.api_key
    if args.model:
        config["model"] = args.model
    if args.timeout is not None:
        config["timeout_seconds"] = args.timeout
    if args.system_prompt:
        config["system_prompt"] = args.system_prompt
    if args.extra_body_json:
        config["extra_body"] = merge_mappings(
            config.get("extra_body"),
            parse_json_mapping(args.extra_body_json, "--extra-body-json"),
        )
    if args.extra_headers_json:
        config["extra_headers"] = merge_mappings(
            config.get("extra_headers"),
            parse_json_mapping(args.extra_headers_json, "--extra-headers-json"),
        )

    base_url = (config.get("base_url") or "").strip()
    api_key = (config.get("api_key") or "").strip()
    model = (config.get("model") or "").strip() or "grok-2-latest"
    timeout_seconds = float(config.get("timeout_seconds") or 60)
    system_prompt = config.get("system_prompt") or DEFAULT_SYSTEM_PROMPT
    extra_body = config.get("extra_body") or {}
    extra_headers = config.get("extra_headers") or {}

    if not base_url:
        raise GrokSearchCliError(
            {"ok": False, "error": "missing_base_url", "detail": "Set base_url in config or GROK_BASE_URL."}
        )
    if not api_key:
        raise GrokSearchCliError(
            {"ok": False, "error": "missing_api_key", "detail": "Set api_key in config or GROK_API_KEY."}
        )

    return {
        "base_url": base_url,
        "api_key": api_key,
        "model": model,
        "timeout_seconds": timeout_seconds,
        "system_prompt": system_prompt,
        "extra_body": extra_body,
        "extra_headers": extra_headers,
    }


def load_json_file(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
            if not isinstance(data, dict):
                raise GrokSearchCliError(
                    {
                        "ok": False,
                        "error": "invalid_config_shape",
                        "detail": f"Config file {path} must contain a JSON object.",
                    }
                )
            return data
    except json.JSONDecodeError as exc:
        raise GrokSearchCliError(
            {"ok": False, "error": "config_parse_error", "detail": f"{path}: {exc}"}
        ) from exc


def parse_json_mapping(raw: Optional[str], source: str) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GrokSearchCliError(
            {"ok": False, "error": "invalid_json", "detail": f"{source} is not valid JSON: {exc}"}
        ) from exc
    if not isinstance(data, dict):
        raise GrokSearchCliError(
            {"ok": False, "error": "invalid_json_type", "detail": f"{source} must be a JSON object."}
        )
    return data


def merge_mappings(base: Optional[Mapping[str, Any]], override: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    if isinstance(base, Mapping):
        merged.update(base)
    if isinstance(override, Mapping):
        merged.update(override)
    return merged


def build_endpoint(base_url: str) -> str:
    cleaned = base_url.rstrip("/")
    if cleaned.endswith("/v1"):
        return f"{cleaned}/chat/completions"
    return f"{cleaned}/v1/chat/completions"


def call_grok(
    *,
    query: str,
    base_url: str,
    api_key: str,
    model: str,
    timeout: float,
    system_prompt: str,
    extra_body: Mapping[str, Any],
    extra_headers: Mapping[str, str],
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": query},
        ],
        "temperature": 0,
        "stream": False,
    }
    payload.update(extra_body or {})

    headers: Dict[str, str] = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    headers.update({k: str(v) for k, v in (extra_headers or {}).items()})

    endpoint = build_endpoint(base_url)

    try:
        response = requests.post(endpoint, headers=headers, json=payload, timeout=timeout)
    except requests.Timeout as exc:
        raise GrokSearchCliError({"ok": False, "error": "timeout", "detail": str(exc)}) from exc
    except requests.RequestException as exc:
        raise GrokSearchCliError({"ok": False, "error": "network_error", "detail": str(exc)}) from exc

    if response.status_code >= 400:
        detail = safe_truncate(response.text.strip(), 400)
        raise GrokSearchCliError(
            {
                "ok": False,
                "error": "http_error",
                "status_code": response.status_code,
                "detail": detail or f"HTTP {response.status_code}",
            }
        )

    try:
        payload_json = response.json()
    except ValueError as exc:
        raise GrokSearchCliError(
            {
                "ok": False,
                "error": "invalid_json_response",
                "detail": safe_truncate(response.text, 400),
            }
        ) from exc

    content = extract_message_text(payload_json)
    sources = extract_sources(payload_json)

    return {
        "ok": True,
        "content": content,
        "sources": sources,
        "raw": payload_json,
    }


def extract_message_text(payload_json: Mapping[str, Any]) -> str:
    choices = payload_json.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message", {})
        if isinstance(message, Mapping):
            content = message.get("content")
            if isinstance(content, str):
                return content.strip()
            if isinstance(content, list):
                parts: List[str] = []
                for part in content:
                    if isinstance(part, Mapping):
                        if part.get("type") == "text" and isinstance(part.get("text"), str):
                            parts.append(part["text"])
                return "\n".join(p.strip() for p in parts if p)
    return json.dumps(payload_json, ensure_ascii=False)


def extract_sources(payload_json: Mapping[str, Any]) -> List[Dict[str, Any]]:
    potential_sources: Iterable[Any] = ()
    choices = payload_json.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message", {})
        if isinstance(message, Mapping):
            for key in ("citations", "sources", "references"):
                value = message.get(key) or message.get("metadata", {}).get(key)
                if value:
                    potential_sources = value  # type: ignore[assignment]
                    break
    if not potential_sources:
        for key in ("sources", "citations", "references"):
            value = payload_json.get(key)
            if value:
                potential_sources = value  # type: ignore[assignment]
                break

    normalized: List[Dict[str, Any]] = []
    if isinstance(potential_sources, list):
        for item in potential_sources:
            normalized_item = normalize_source_entry(item)
            if normalized_item:
                normalized.append(normalized_item)
    return normalized


def normalize_source_entry(entry: Any) -> Optional[Dict[str, Any]]:
    if isinstance(entry, str):
        return {"url": entry}
    if isinstance(entry, Mapping):
        url = entry.get("url") or entry.get("href")
        title = entry.get("title") or entry.get("name")
        snippet = entry.get("snippet") or entry.get("quote")
        normalized: Dict[str, Any] = {}
        if url:
            normalized["url"] = url
        if title:
            normalized["title"] = title
        if snippet:
            normalized["snippet"] = snippet
        return normalized if normalized else None
    return None


def safe_truncate(value: Optional[str], limit: int) -> str:
    if not value:
        return ""
    return value if len(value) <= limit else value[: limit - 3] + "..."


def main() -> int:
    try:
        payload = execute()
    except GrokSearchCliError as exc:
        print(json.dumps(exc.payload, ensure_ascii=False))
        return exc.exit_code
    except Exception as exc:  # pragma: no cover - ensure JSON output for unexpected errors
        print(
            json.dumps(
                {"ok": False, "error": "unexpected_exception", "detail": str(exc)},
                ensure_ascii=False,
            )
        )
        return 1

    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
