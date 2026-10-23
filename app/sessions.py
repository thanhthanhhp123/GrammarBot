from collections import OrderedDict

from app.config import MAX_HISTORY_TURNS

MAX_SESSIONS = 500

# No lock needed: these functions never await, so asyncio can't interleave them.
# ponytail: in-memory and single-process, so run one uvicorn worker; a restart clears history.
_sessions: OrderedDict[str, list[dict[str, str]]] = OrderedDict()


def get_history(session_id: str) -> list[dict[str, str]]:
    return list(_sessions.get(session_id, []))


def add_turn(session_id: str, user: str, assistant: str) -> None:
    history = _sessions.pop(session_id, [])
    history += [{"role": "user", "content": user}, {"role": "assistant", "content": assistant}]
    _sessions[session_id] = history[max(0, len(history) - 2 * MAX_HISTORY_TURNS):]
    if len(_sessions) > MAX_SESSIONS:
        _sessions.popitem(last=False)  # drop the least recently used session
