def __getattr__(name: str):
    if name in ("main", "run"):
        from .bootstrap.backend import main, run

        globals()["main"] = main
        globals()["run"] = run
        return globals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["main", "run"]
