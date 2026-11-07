"""OpenAI HTTP client construction; dispatch/retries belong to the gateway."""
import httpx

from text_content.pipeline_runtime import OPENAI_KEY_MISSING

OPENAI_PROVIDERS = ('openai', 'openai-responses', 'openai-chat-completions')


def create_openai_client(api_key):
    """Create a client only after an explicitly supplied evaluation key is checked."""
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError(OPENAI_KEY_MISSING)
    return httpx.Client(
        base_url='https://api.openai.com/v1/',
        headers={'Authorization': f'Bearer {api_key}'},
        timeout=httpx.Timeout(600, connect=10),
        follow_redirects=False,
        transport=httpx.HTTPTransport(retries=0),
    )
