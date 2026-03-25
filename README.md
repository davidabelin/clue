## clue

Standalone Clue lab for AIX.

### Scope of this initial implementation
- Deterministic, event-sourced classic Clue turn engine
- Seat-token multiplayer web UI with polling updates
- Human, heuristic, and OpenAI-backed LLM seats under one rules authority
- Standalone Flask app that also mounts cleanly into AIX under `/clue`

### Layout
- `clue_core/`
- `clue_agents/`
- `clue_storage/`
- `clue_web/`
- `tests/`
- `docs/`
- `data/`

### Local run
```powershell
pip install -r requirements.txt
python run.py
```

Then open `http://127.0.0.1:5002/`.
