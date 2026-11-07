"""Translate the shared Messages interface; leave native usage to ai_pricing."""


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list) and all(
        isinstance(block, dict) and set(block) == {"type", "text"}
        and block["type"] == "text" and isinstance(block["text"], str)
        for block in content
    ):
        return "\n".join(block["text"] for block in content)
    raise ValueError("OpenAI system content must be plain text")


def _content(content, chat):
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        raise ValueError("OpenAI message content must be text or a list of blocks")
    converted = []
    for block in content:
        if not isinstance(block, dict):
            raise ValueError("OpenAI message blocks must be objects")
        kind = block.get("type")
        if kind == "text" and set(block) == {"type", "text"} and isinstance(block["text"], str):
            converted.append({"type": "text" if chat else "input_text", "text": block["text"]})
        elif kind == "image" and set(block) == {"type", "source"}:
            source = block["source"]
            if not isinstance(source, dict):
                raise ValueError("OpenAI image source must be an object")
            if (source.get("type") == "base64"
                    and set(source) == {"type", "media_type", "data"}
                    and isinstance(source["media_type"], str)
                    and source["media_type"].startswith("image/")
                    and isinstance(source["data"], str) and source["data"]):
                url = f"data:{source['media_type']};base64,{source['data']}"
            elif source.get("type") == "url" and set(source) == {"type", "url"} and isinstance(source["url"], str):
                url = source["url"]
            else:
                raise ValueError("OpenAI images require a base64 image or URL source")
            converted.append({"type": "image_url", "image_url": {"url": url}} if chat
                             else {"type": "input_image", "image_url": url})
        else:
            raise ValueError(f"Unsupported OpenAI message block: {kind!r}")
    return converted


def prepare_request(request, provider):
    """Validate before reservation, so unsupported options cannot look charged."""
    chat = provider == "openai-chat-completions"
    unsupported = {"top_k"} | (set() if chat else {"stop_sequences"})
    if unsupported.intersection(request):
        raise ValueError(f"Unsupported {provider} options: {sorted(unsupported.intersection(request))}")
    messages = [{"role": message["role"], "content": _content(message["content"], chat)}
                for message in request["messages"]]
    payload = {"model": request["model"], "messages" if chat else "input": messages,
               "max_completion_tokens" if chat else "max_output_tokens": request["max_tokens"]}
    if "system" in request:
        system = _text(request["system"])
        if chat:
            messages.insert(0, {"role": "system", "content": system})
        else:
            payload["instructions"] = system
    for key in ("temperature", "top_p"):
        if key in request:
            payload[key] = request[key]
    if "stop_sequences" in request:
        payload["stop"] = request["stop_sequences"]
    return ("chat/completions" if chat else "responses"), payload


def response_content(raw, provider):
    """Adapt only after persistence/settlement, including on a cache replay.

    Text fragments form one block because production helpers read the first
    text block. Refusals and failed responses never become usable extraction.
    Native usage, output blocks and IDs remain untouched in the ledger.
    """
    if provider == "openai-chat-completions":
        choices = raw.get("choices")
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            raise ValueError("Malformed OpenAI response: expected one completion")
        choice = choices[0]
        message = choice.get("message")
        if not isinstance(message, dict):
            raise ValueError("Malformed OpenAI completion message")
        reason = choice.get("finish_reason")
        if message.get("refusal") or reason == "content_filter":
            return [], "refusal"
        if reason not in {"stop", "length"}:
            raise ValueError(f"Unsupported OpenAI completion finish reason: {reason!r}")
        text = message.get("content")
        if not isinstance(text, str):
            raise ValueError("Malformed OpenAI completion text")
        return [{"type": "text", "text": text}], "max_tokens" if reason == "length" else "end_turn"

    status = raw.get("status")
    details = raw.get("incomplete_details") or {}
    if status == "incomplete":
        if not isinstance(details, dict) or details.get("reason") != "max_output_tokens":
            return [], "incomplete"
        stop = "max_tokens"
    elif status == "completed":
        stop = "end_turn"
    elif status in {"failed", "cancelled"}:
        return [], status
    else:
        raise ValueError(f"Malformed OpenAI response status: {status!r}")
    output = raw.get("output")
    if not isinstance(output, list):
        raise ValueError("Malformed OpenAI response output")
    texts = []
    for item in output:
        if not isinstance(item, dict):
            raise ValueError("Malformed OpenAI output item")
        if item.get("type") == "reasoning":
            continue
        if item.get("type") != "message" or not isinstance(item.get("content"), list):
            raise ValueError("Unsupported OpenAI output item")
        for block in item["content"]:
            if not isinstance(block, dict):
                raise ValueError("Malformed OpenAI output block")
            if block.get("type") == "refusal":
                return [], "refusal"
            if block.get("type") != "output_text" or not isinstance(block.get("text"), str):
                raise ValueError("Malformed OpenAI output text")
            texts.append(block["text"])
    return ([{"type": "text", "text": "".join(texts)}] if texts else []), stop
