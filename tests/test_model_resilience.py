"""Engine recovery with the REAL model on DirectML (marker ``model``).

Simulates the device lost after suspending the laptop: the real ONNX Runtime
session starts raising an onnxruntime error on every ``run``. The EngineGuard
must rebuild the session (fresh DirectML), retry and return the same vector
as before the failure.
"""

import numpy as np
import pytest

pytestmark = pytest.mark.model


@pytest.fixture(scope="module")
def cfg():
    from recall import config

    c = config.load()
    try:
        from recall.embedder import from_config

        from_config(c, "query")
    except FileNotFoundError:
        pytest.skip("no local model")
    return c


class _Holder:
    def __init__(self, emb):
        self.emb = emb


def test_lost_device_is_rebuilt_and_retried(cfg):
    from onnxruntime.capi.onnxruntime_pybind11_state import Fail

    from recall.embedder import format_query, from_config
    from recall.service import EngineGuard

    holder = _Holder(from_config(cfg, "query"))
    q = [format_query(holder.emb, "bounded context")]
    before = holder.emb.embed(q, "query")

    class DeadSession:
        """The old session after the device is lost: every ``run`` fails."""

        def __init__(self, real):
            self._real = real

        def run(self, *a, **kw):
            raise Fail("887A0005 DXGI_ERROR_DEVICE_REMOVED (simulated)")

        def __getattr__(self, name):
            return getattr(self._real, name)

    broken = holder.emb
    broken._sess = DeadSession(broken._sess)
    logs = []
    guard = EngineGuard(
        holder, rebuild=lambda: from_config(cfg, "query"), give_up=lambda: None, log=lambda w, e: logs.append(w)
    )
    after = guard.run(lambda: holder.emb.embed(q, "query"))
    assert holder.emb is not broken and guard.rebuilds == 1 and not guard.gave_up
    assert logs == ["search", "rebuild"]
    assert "DmlExecutionProvider" in holder.emb.providers
    assert float(np.dot(before[0], after[0])) >= 0.9999
