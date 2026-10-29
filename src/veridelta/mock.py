"""A fake model for offline tests and dry runs.

``MockClient`` has the same call shape as the Anthropic client (``client.messages.create``). For a
Stage 3 prompt it answers the structural prior of every class; for any other prompt it answers a
fixed single-category reply. It costs nothing and makes the whole pipeline testable without a key.
"""
import json
import re

LINE = re.compile(r"depth=\d+\s+(\S+)\s+type=(\S+)\s+prior=(.+)$")


class _Obj:
    pass


class _Messages:
    @staticmethod
    def create(**kw):
        prompt = kw["messages"][0]["content"]
        if "BLAST RADIUS" in prompt:
            out = {}
            for ln in prompt.splitlines():
                m = LINE.match(ln.strip())
                if m and m.group(3).strip() != "none":
                    out[m.group(1)] = {"categories": [m.group(3).strip()], "confidence": 0.6}
            text = json.dumps(out)
        else:
            text = '{"categories": ["Sequence Invalidation"], "confidence": 0.5}'
        reply, block = _Obj(), _Obj()
        block.text = text
        reply.content = [block]
        reply.stop_reason = "end_turn"
        return reply


class MockClient:
    messages = _Messages
