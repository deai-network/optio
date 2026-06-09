"""Exception types raised by optio-core to parent task code."""


class ChildProcessFailed(Exception):
    """Raised by ProcessContext.run_child when a child task fails and
    survive_failure=False. Carries the child's identifying name, its
    process_id, and the original exception raised inside the child's
    execute function.

    Parents catch this and branch on isinstance(e.original, SomeType):

        try:
            await ctx.run_child(...)
        except ChildProcessFailed as e:
            if isinstance(e.original, DownloadFailed):
                ...
    """

    def __init__(self, name: str, process_id: str, original: BaseException):
        self.name = name
        self.process_id = process_id
        self.original = original
        super().__init__(
            f"Child '{name}' (process_id={process_id}) failed: {original!r}"
        )


class LaunchError(Exception):
    """Raised by Optio.launch_and_await_result when the launch itself is
    refused (the LaunchOutcome was not ok). ``reason`` carries the typed
    LaunchOutcome reason string (e.g. "not-found", "not-launchable")."""

    def __init__(self, process_id: str, reason: str):
        self.process_id = process_id
        self.reason = reason
        super().__init__(f"launch of '{process_id}' refused: {reason}")


class ResultNotPublished(Exception):
    """Raised by Optio.launch_and_await_result when the process reached a
    terminal state without ever calling ctx.publish_result."""

    def __init__(self, process_id: str):
        self.process_id = process_id
        super().__init__(
            f"process '{process_id}' ended without publishing a result"
        )
