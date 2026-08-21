"""Root conftest.

``pytester`` must be enabled from the top-level conftest (pytest refuses
``pytest_plugins`` anywhere else). It gives ``tests/test_pytest_plugin.py`` a
throwaway pytest run to assert against — the only honest way to test a plugin
whose whole job is changing another run's exit status.
"""

pytest_plugins = ["pytester"]
