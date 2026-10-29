"""Minimal Anthropic Messages API client (stdlib only) with the same call shape as the SDK:
client.messages.create(model=, max_tokens=, messages=[...], temperature=) -> .content[0].text, .stop_reason, .usage"""
import json, os, ssl, urllib.request, urllib.error
class _Obj:
    def __init__(self, d): self.__dict__.update(d)
class _Err(Exception):
    def __init__(self, code, body): super().__init__(f"HTTP {code}: {body[:300]}"); self.status_code = code
class _Messages:
    def __init__(self, key): self.key = key
    def create(self, **kw):
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=json.dumps(kw).encode(),
            headers={"x-api-key": self.key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        ctx = ssl.create_default_context(cafile=os.environ.get("SSL_CERT_FILE") or None)
        try:
            with urllib.request.urlopen(req, timeout=120, context=ctx) as r: d = json.load(r)
        except urllib.error.HTTPError as e:
            raise _Err(e.code, e.read().decode("utf-8", "ignore"))
        return _Obj({"content": [_Obj(c) for c in d.get("content", [])], "stop_reason": d.get("stop_reason", ""),
                     "usage": _Obj(d.get("usage", {"input_tokens": 0, "output_tokens": 0}))})
class Client:
    def __init__(self, api_key=None): self.messages = _Messages(api_key or os.environ["ANTHROPIC_API_KEY"])
