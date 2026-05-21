"""Client LLM multi-provider per RAG chatbot — con retry, proxy e timeout esteso."""

from __future__ import annotations

import httpx
import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_MODELS = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-3-5-sonnet-20241022",
    "deepseek": "deepseek-chat",
    "google": "gemini-1.5-flash",
    "kimi": "moonshot-v1-128k",
}

PROVIDER_URLS = {
    "openai": "https://api.openai.com/v1/chat/completions",
    "anthropic": "https://api.anthropic.com/v1/messages",
    "deepseek": "https://api.deepseek.com/v1/chat/completions",
    "google": "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
    "kimi": "https://api.moonshot.ai/v1/chat/completions",
}

_TIMEOUT = 120.0
_MAX_RETRIES = 3
_RETRY_DELAY = 2.0


def _get_client() -> httpx.Client:
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    return httpx.Client(
        timeout=_TIMEOUT,
        follow_redirects=True,
        proxy=proxy,
    )


def _build_prompt(context: str, query: str) -> str:
    return (
        "ISTRUZIONE CRITICA: Se la domanda chiede 'ricordi...', 'cosa abbiamo detto prima...', "
        "'all'inizio...', rispondi usando SOLO la cronologia della conversazione qui sopra. "
        "NON usare i Documenti del progetto per rispondere a domande sulla conversazione.\n\n"
        "I 'Foundation Documents' sono i documenti principali del progetto (README, AGENTS.md, docs):\n"
        "usali come fonte primaria per la panoramica. Gli 'excerpt' indicizzati sono per dettagli specifici.\n"
        "Cita le fonti in modo naturale, integrandole nel discorso. Non fare elenchi numerati di documenti citati.\n"
        "Se la domanda chiede esplicitamente quali file implementano una feature, elencali in modo naturale.\n\n"
        f"Documenti del progetto (fonte esterna, NON conversazione):\n{context}\n\n"
        f"Domanda: {query}\n\n"
        "Rispondi in italiano se la domanda è in italiano, altrimenti nella lingua della domanda."
    )


def _build_messages(
    context: str,
    query: str,
    history: list[dict[str, str]] | None,
    summary: str | None = None,
) -> list[dict[str, str]]:
    system = (
        "Sei un assistente esperto che risponde basandosi sui documenti forniti e sulla conversazione.\n"
        "ISTRUZIONE CRITICA: Se la domanda chiede 'ricordi...', 'cosa abbiamo detto prima...', "
        "'all'inizio...', rispondi usando SOLO la cronologia della conversazione qui sopra. "
        "NON usare i Documenti del progetto per rispondere a domande sulla conversazione.\n"
        "I 'Foundation Documents' sono i documenti principali del progetto (README, AGENTS.md, docs):\n"
        "usali come fonte primaria per la panoramica. Gli 'excerpt' indicizzati sono per dettagli specifici.\n"
        "Cita le fonti in modo naturale (es. 'come descritto in recursive-orchestrator-design.md'),\n"
        "integrandole nel discorso. NON fare elenchi numerati di documenti citati.\n"
        "Se la domanda chiede esplicitamente quali file implementano una feature, elencali in modo naturale."
    )
    if summary:
        system += f"\n\nRiassunto conversazione precedente:\n{summary}"
    messages: list[dict[str, str]] = [{"role": "system", "content": system}]
    if history:
        messages.extend(history)
    # Documenti RAG in un messaggio user separato, chiaramente etichettato
    messages.append(
        {"role": "user", "content": f"Documenti del progetto (fonte esterna, NON conversazione):\n\n{context}"}
    )
    messages.append({"role": "user", "content": f"Domanda: {query}"})
    return messages


def _post_with_retry(
    client: httpx.Client,
    url: str,
    headers: dict,
    payload: dict,
    params: dict | None = None,
) -> httpx.Response:
    last_err = None
    kwargs = {"headers": headers, "json": payload}
    if params:
        kwargs["params"] = params
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            logger.info("LLM request attempt %d/%d to %s", attempt, _MAX_RETRIES, url)
            resp = client.post(url, **kwargs)
            resp.raise_for_status()
            return resp
        except httpx.TimeoutException as exc:
            last_err = exc
            logger.warning("Timeout on attempt %d/%d: %s", attempt, _MAX_RETRIES, exc)
        except httpx.ConnectError as exc:
            last_err = exc
            logger.warning("Connect error on attempt %d/%d: %s", attempt, _MAX_RETRIES, exc)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in (429, 500, 502, 503, 504):
                last_err = exc
                logger.warning("HTTP %d on attempt %d/%d", status, attempt, _MAX_RETRIES)
            else:
                raise
        if attempt < _MAX_RETRIES:
            time.sleep(_RETRY_DELAY * attempt)
    raise last_err


def _call_openai_format(
    url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    max_tokens: int = 2000,
    temperature: float = 0.3,
) -> str:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    logger.info(
        "LLM call -> url=%s model=%s key_prefix=%s messages_count=%d",
        url, model, api_key[:8] if api_key else "NONE", len(messages)
    )
    with _get_client() as client:
        resp = _post_with_retry(client, url, headers, payload)
        data = resp.json()
        return data["choices"][0]["message"]["content"]


def _call_anthropic(api_key: str, model: str, system: str, query: str, max_tokens: int = 2000, temperature: float = 0.3) -> str:
    url = PROVIDER_URLS["anthropic"]
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "system": system,
        "messages": [{"role": "user", "content": query}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    with _get_client() as client:
        resp = _post_with_retry(client, url, headers, payload)
        data = resp.json()
        return data["content"][0]["text"]


def _call_google(api_key: str, model: str, prompt: str, max_tokens: int = 2000, temperature: float = 0.3) -> str:
    url = PROVIDER_URLS["google"].format(model=model)
    params = {"key": api_key}
    payload = {
        "contents": [
            {"role": "user", "parts": [{"text": prompt}]}
        ],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        },
    }
    with _get_client() as client:
        resp = _post_with_retry(client, url, {}, payload, params)
        data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]


def _summarize_conversation(
    provider: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
) -> str:
    """Riassume una lista di messaggi in un paragrafo conciso."""
    conv_text = "\n\n".join(
        f"[{m['role']}] {m['content'][:800]}" for m in messages
    )
    prompt = (
        "Riassumi la seguente conversazione in modo conciso (massimo 5 frasi). "
        "Mantieni SOLO le informazioni tecniche, decisioni prese, e domande/risposte importanti. "
        "Scarta saluti, ringraziamenti, e ripetizioni.\n\n"
        f"{conv_text}"
    )

    if provider in ("openai", "deepseek", "kimi"):
        summ_messages = [{"role": "user", "content": prompt}]
        return _call_openai_format(
            PROVIDER_URLS[provider], api_key, model, summ_messages,
            max_tokens=300, temperature=0.1,
        )

    if provider == "anthropic":
        return _call_anthropic(
            api_key, model, "", prompt, max_tokens=300, temperature=0.1,
        )

    if provider == "google":
        return _call_google(
            api_key, model, prompt, max_tokens=300, temperature=0.1,
        )

    raise ValueError(f"Provider non supportato per summarization: {provider}")


def chat_with_llm(
    provider: str,
    api_key: str,
    model: str | None,
    context: str,
    query: str,
    history: list[dict[str, str]] | None = None,
) -> str:
    """Chiama il provider LLM e restituisce la risposta testuale."""
    model = model or DEFAULT_MODELS.get(provider, "")
    if not model:
        raise ValueError(f"Modello non specificato e nessun default per provider: {provider}")

    # ── Summarization progressiva ───────────────────────────────────────────
    summary = None
    if history:
        total_chars = sum(len(m.get("content", "")) for m in history)
        if len(history) > 12 or total_chars > 6000:
            try:
                old_messages = history[:-6]
                recent_messages = history[-6:]
                summary = _summarize_conversation(provider, api_key, model, old_messages)
                history = recent_messages
                logger.info(
                    "History summarized: %d old messages -> %d chars summary",
                    len(old_messages), len(summary),
                )
            except Exception as exc:
                logger.warning(
                    "Summarization failed, falling back to last 10 messages: %s", exc,
                )
                history = history[-10:]

    if provider in ("openai", "deepseek", "kimi"):
        messages = _build_messages(context, query, history, summary)
        return _call_openai_format(PROVIDER_URLS[provider], api_key, model, messages)

    if provider == "anthropic":
        system = (
            "Sei un assistente esperto che risponde basandosi esclusivamente sui documenti forniti. "
            "Se la risposta non è nei documenti, dillo chiaramente. "
            "Cita i numeri dei documenti fonte."
        )
        if summary:
            system += f"\n\nRiassunto conversazione precedente:\n{summary}"
        user_content = _build_prompt(context, query)
        if history:
            hist_text = "\n\n".join(f"[{m['role']}] {m['content']}" for m in history)
            user_content = f"{hist_text}\n\n{user_content}"
        return _call_anthropic(api_key, model, system, user_content)

    if provider == "google":
        prompt = _build_prompt(context, query)
        if summary:
            prompt = f"Riassunto conversazione precedente:\n{summary}\n\n{prompt}"
        if history:
            hist_text = "\n\n".join(f"[{m['role']}] {m['content']}" for m in history)
            prompt = f"{hist_text}\n\n{prompt}"
        return _call_google(api_key, model, prompt)

    raise ValueError(f"Provider non supportato: {provider}")
