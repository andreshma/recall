"""F-09: DirectML adapter choice. No hard-coded adapter; fall back across adapters, then CPU; log it."""

import pytest

from recall import config
from recall.embedder import _session_options, open_session, provider_attempts

AVAIL = ["DmlExecutionProvider", "CPUExecutionProvider"]


class FakeOrt:
    """Minimal ``onnxruntime`` stand-in: only the listed DirectML adapters exist.

    ``silent`` mimics the real onnxruntime-directml 1.24: a missing adapter does
    not raise, the session comes up on CPU only (measured on this machine).
    """

    def __init__(self, adapters, available=AVAIL, silent=False):
        self.adapters = set(adapters)
        self.silent = silent
        self.available = list(available)
        self.tried = []

    def get_available_providers(self):
        return self.available

    def InferenceSession(self, model, so, providers):
        self.tried.append(providers)
        names = []
        for p in providers:
            name, opts = (p, {}) if isinstance(p, str) else p
            if name == "DmlExecutionProvider" and opts.get("device_id", 0) not in self.adapters:
                if not self.silent:
                    raise RuntimeError("D3D12CreateDevice failed")
                continue
            names.append(name)
        return type("Sess", (), {"get_providers": lambda self: names})()


def test_defaults_name_no_adapter():
    for role in ("index_providers", "query_providers"):
        assert config.DEFAULTS["model"][role] == ["DmlExecutionProvider", "CPUExecutionProvider"]


def test_attempt_order():
    assert provider_attempts(["DmlExecutionProvider:1", "CPUExecutionProvider"], AVAIL)[:3] == [
        [("DmlExecutionProvider", {"device_id": 1}), "CPUExecutionProvider"],
        [("DmlExecutionProvider", {"device_id": 0}), "CPUExecutionProvider"],
        [("DmlExecutionProvider", {"device_id": 2}), "CPUExecutionProvider"],
    ]
    assert provider_attempts(["DmlExecutionProvider", "CPUExecutionProvider"], AVAIL)[-1] == ["CPUExecutionProvider"]
    assert provider_attempts(["DmlExecutionProvider:1"], ["CPUExecutionProvider"]) == [["CPUExecutionProvider"]]
    assert provider_attempts(["CPUExecutionProvider"], AVAIL) == [["CPUExecutionProvider"]]


@pytest.mark.parametrize("silent", [False, True])
def test_missing_adapter_falls_back_to_another_adapter_before_cpu(silent):
    """The old code went straight to CPU (silently) when adapter 1 did not exist."""
    ort, logs = FakeOrt(adapters={0}, silent=silent), []
    sess, dev = open_session(ort, "m.onnx", None, ["DmlExecutionProvider:1", "CPUExecutionProvider"], logs.append)
    assert dev == 0 and "DmlExecutionProvider" in sess.get_providers()
    assert len(logs) == 1 and "DirectML adapter 0" in logs[0] and "DmlExecutionProvider:1" in logs[0]


def test_no_adapter_at_all_ends_on_cpu_and_says_so():
    ort, logs = FakeOrt(adapters=set()), []
    sess, dev = open_session(ort, "m.onnx", None, ["DmlExecutionProvider", "CPUExecutionProvider"], logs.append)
    assert dev is None and sess.get_providers() == ["CPUExecutionProvider"]
    assert ort.tried[-1] == ["CPUExecutionProvider"] and len(ort.tried) == 5
    assert "CPUExecutionProvider" in logs[0] and "failed first" in logs[0]


def test_logs_to_stderr_by_default(capsys):
    open_session(FakeOrt(adapters={0, 1}), "m.onnx", None, ["DmlExecutionProvider:1"])
    assert "DirectML adapter 1" in capsys.readouterr().err


def test_cpu_failure_is_not_hidden():
    class Broken(FakeOrt):
        def InferenceSession(self, model, so, providers):
            raise RuntimeError("bad model")

    with pytest.raises(RuntimeError, match="bad model"):
        open_session(Broken(adapters={0}), "m.onnx", None, ["CPUExecutionProvider"], lambda m: None)


def test_session_options_keep_mem_pattern_on_cpu_only():
    """The CPU fallback must not inherit DirectML's disabled memory pattern."""
    import onnxruntime as ort

    assert _session_options(ort, dml=False).enable_mem_pattern is True
    assert _session_options(ort, dml=True).enable_mem_pattern is False


def test_open_session_builds_cpu_options_on_fallback():
    """With no DirectML adapter, the factory is asked for CPU (dml=False) options."""
    calls = []

    def factory(is_dml):
        calls.append(is_dml)
        return object()  # FakeOrt ignores the options object

    _sess, dev = open_session(
        FakeOrt(adapters=set()),
        "m.onnx",
        factory,
        ["DmlExecutionProvider", "CPUExecutionProvider"],
        lambda m: None,
    )
    assert dev is None  # ended on CPU
    assert calls[-1] is False  # the CPU attempt was built with dml=False
    assert True in calls  # the DirectML attempts were built with dml=True
