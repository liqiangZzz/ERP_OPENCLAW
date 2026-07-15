"""Regression tests for the custom OpenSandbox backend."""

from types import SimpleNamespace

from agent.backends.custom_opensandbox import OpenSandboxBackend


class FakeCommands:
    """Record commands and return a minimal successful SDK response."""

    def __init__(self) -> None:
        self.commands: list[str] = []

    def run(self, command: str):
        self.commands.append(command)
        return SimpleNamespace(
            exit_code=0,
            logs=SimpleNamespace(stdout=[], stderr=[]),
        )


def test_execute_uses_default_timeout_without_attribute_error() -> None:
    """The backend must initialize the timeout attribute used by execute()."""
    commands = FakeCommands()
    sandbox = SimpleNamespace(id="sandbox-test", commands=commands)
    backend = OpenSandboxBackend(sandbox=sandbox, timeout=123)

    result = backend.execute("echo ok")

    assert result.exit_code == 0
    assert backend._default_timeout == 123
    assert commands.commands == [
        f'export PATH="{backend.SANDBOX_PATH}:$PATH" && echo ok'
    ]
