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


def test_the_application_imports_and_routes_build():
    from app.main import app

    paths = {route.path for route in app.routes if hasattr(route, "path")}
    # A handful of landmarks, so a silently empty router table fails too.
    assert "/health" in paths
    assert any(p.startswith("/shared/") for p in paths)


def test_every_registered_router_module_imports():
    """Each router in main's import list, loaded on its own.

    A NameError at module scope in any one of them stops the backend from starting.
    """
    import importlib
    import pkgutil

    import app.routers as routers_pkg

    failures = []
    for module in pkgutil.iter_modules(routers_pkg.__path__):
        try:
            importlib.import_module(f"app.routers.{module.name}")
        except Exception as err:  # noqa: BLE001 — collecting all of them beats stopping at one
            failures.append(f"{module.name}: {type(err).__name__}: {err}")
    assert not failures, "routers that will not import:\n" + "\n".join(failures)
