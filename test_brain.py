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

with tempfile.TemporaryDirectory() as d:
    brain.FILE_ROOTS, brain.BACKUP_DIR = [Path(d)], Path(d) / "backups"
    f = Path(d) / "sub" / "notes.txt"
    assert "Created" in brain.write_file(str(f), "hello world")
    assert brain.read_file(str(f)) == "hello world"
    assert "Edited" in brain.edit_file(str(f), "world", "Jarvis")
    assert f.read_text(encoding="utf-8") == "hello Jarvis"
    assert "not found" in brain.edit_file(str(f), "nope", "x")
    f.write_text("a a", encoding="utf-8")
    assert "2 times" in brain.edit_file(str(f), "a", "b") and f.read_text(encoding="utf-8") == "a a"
    assert "Overwrote" in brain.write_file(str(f), "new")
    assert len(list(brain.BACKUP_DIR.iterdir())) == 2  # one per edit/overwrite
    assert "notes.txt" in brain.list_files(str(f.parent))
    assert "outside the allowed folders" in brain.run_tool("read_file", '{"path": "C:/Windows/win.ini"}', ui=None)
    assert "outside the allowed folders" in brain.run_tool("write_file", '{"path": "Desktop/x.txt", "content": ""}', ui=None)

assert "Error in nope" in brain.run_tool("nope", "{}", ui=None)
assert "Error in open_app" in brain.run_tool("open_app", "not json", ui=None)

# History trimming never starts on an orphaned tool result.
hist = [{"role": "user", "content": "a"}, {"role": "assistant", "tool_calls": []},
        {"role": "tool", "content": "r"}, {"role": "assistant", "content": "x"}, {"role": "user", "content": "b"}]
assert brain.recent(hist, limit=3) == [{"role": "user", "content": "b"}]
assert brain.recent(hist, limit=5) == hist

# "I did it" text without a tool call is caught; normal chat is not.
for claim in ["Word is now open!", "I've opened Paint", "I've created notlar.txt", "Word'ü açtım", "I'll remember that"]:
    assert brain.claims_action(claim), claim
for chat in ["The capital of France is Paris.", "15 times 3 is 45.", "Merhaba! Nasıl yardımcı olabilirim?"]:
    assert not brain.claims_action(chat), chat

# The answer is built from real tool results; empty means nothing happened.
assert brain.compose_reply(["Opened https://gmail.com."], ["Anything else?"]) == "Opened https://gmail.com.\nAnything else?"
assert brain.compose_reply([], []) == brain.NOTHING_DONE

print("all checks passed")
