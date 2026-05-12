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
