"""The app actually assembles.

Added after a module-level NameError in one router took the whole backend down at startup
while the suite stayed green: every test imports the routers it exercises directly, and
nothing imported `app.main`, so an error in a module none of them touched was invisible
here and fatal in the app.

This is deliberately shallow. It does not exercise behaviour — it asserts that importing
the application and building its route table raises nothing, which is the exact failure
that slipped through.
"""

from __future__ import annotations


def _is_missing_optional_dependency(err: BaseException) -> bool:
    """True when an import failed only because a third-party package is absent.

    CI installs a deliberately small dependency set — assembled from what the suite
    actually needs, not from reading imports — so several routers legitimately cannot be
    imported there (torch, telegram, the video stack). That is an environment fact, not a
    defect in the module.

    A missing *first-party* module is still a defect: it means something references a file
    that is not there, which breaks the packaged app too.
    """
    if not isinstance(err, ImportError):
        return False

    # ModuleNotFoundError carries .name; a bare ImportError from a nested loader may not,
    # so fall back to its message. Both shapes occur in practice.
    missing = err.name or ""
    if not missing:
        text = str(err)
        marker = "No module named "
        if marker not in text:
            return False  # e.g. "cannot import name X" — a real version mismatch
        missing = text.split(marker, 1)[1].strip().strip("'\"")

    return missing.split(".")[0] not in {"app", "vendor"}


def test_the_application_imports_and_routes_build():
    """The app assembles and its route table is populated."""
    import pytest

    try:
        from app.main import app
    except ModuleNotFoundError as err:
        if _is_missing_optional_dependency(err):
            pytest.skip(f"not installed in this environment: {err.name}")
        raise

    paths = {route.path for route in app.routes if hasattr(route, "path")}
    # A handful of landmarks, so a silently empty router table fails too.
    assert "/health" in paths
    assert any(p.startswith("/shared/") for p in paths)


def test_every_registered_router_module_imports():
    """Each router loaded on its own, so a module-scope error cannot hide.

    This is the test that would have caught a missing `import os` taking the backend down
    at launch: every other test imports the routers it exercises, so an error in a module
    none of them touch is invisible until the app starts.

    Absent optional dependencies are ignored — see _is_missing_optional_dependency. Every
    other failure (NameError, SyntaxError, a bad first-party import) is reported.
    """
    import importlib
    import pkgutil

    import app.routers as routers_pkg

    failures = []
    for module in pkgutil.iter_modules(routers_pkg.__path__):
        try:
            importlib.import_module(f"app.routers.{module.name}")
        except Exception as err:  # noqa: BLE001 — collecting all of them beats stopping at one
            if _is_missing_optional_dependency(err):
                continue
            failures.append(f"{module.name}: {type(err).__name__}: {err}")
    assert not failures, "routers that will not import:\n" + "\n".join(failures)
