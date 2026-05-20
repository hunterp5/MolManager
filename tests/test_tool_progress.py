from __future__ import annotations

import threading

from chemmanager.tool_progress import ToolProgressState


def test_tool_progress_state_threaded_updates():
    state = ToolProgressState()
    state.begin("Calculate descriptors", 100)
    errors: list[Exception] = []

    def worker() -> None:
        try:
            for i in range(1, 101):
                state.update("Calculate descriptors", i, 100)
        except Exception as exc:
            errors.append(exc)

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert not errors
    msg, done, total, active = state.snapshot()
    assert active
    assert done == 100
    assert total == 100
    assert msg == "Calculate descriptors"
    state.end()
    assert state.snapshot()[3] is False
