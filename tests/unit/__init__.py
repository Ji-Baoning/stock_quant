# Package marker so ``tests/unit`` modules import as ``unit.*`` and never
# collide (pytest prepend import mode) with same-named test modules in
# ``tests/integration`` -- e.g. this plan's ``test_acceptance_checks.py``
# exists in both trees.  Unit tests import nothing locally, so the marker is
# otherwise inert; ``tests/integration`` stays package-free because its tests
# import the shared ``conftest`` helpers top-level.
