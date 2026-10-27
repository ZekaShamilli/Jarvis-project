"""Self-check: python test_brain.py"""
import tempfile
from pathlib import Path

import brain

assert brain.parse_action('Sure: {"action": "click", "x": 10, "y": 20}') == {"action": "click", "x": 10, "y": 20}
try:
    brain.parse_action("no json here")
    raise AssertionError("expected ValueError")
except ValueError:
    pass

assert brain.safe_filename('Rapor: "Q3"/2026?') == "Rapor Q32026"
assert brain.safe_filename("...") == "Jarvis document"

assert brain.strip_think("<think>hmm</think>\nTamam, açtım.") == "Tamam, açtım."

with tempfile.TemporaryDirectory() as d:
    path = Path(d) / "m.json"
    assert brain.load_memory(path) == {"history": [], "urls": [], "facts": []}
    m = {"history": [{"role": "user", "content": str(i)} for i in range(100)], "urls": [], "facts": ["x"]}
    brain.save_memory(m, path)
    loaded = brain.load_memory(path)
    assert len(loaded["history"]) == brain.HISTORY_LIMIT and loaded["history"][-1]["content"] == "99"

assert "Error in nope" in brain.run_tool("nope", "{}", ui=None)
assert "Error in open_app" in brain.run_tool("open_app", "not json", ui=None)

print("all checks passed")
