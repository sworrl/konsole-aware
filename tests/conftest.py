"""Make the test suite hermetic.

konsole_aware resolves its config, groups and state paths at import time, so the
environment must be redirected before any test module imports the package. Tests
must never touch the user's real ~/.config/konsole-aware, the live state file, or
the live Konsole session they happen to run in.
"""

import os
import tempfile

_ROOT = tempfile.mkdtemp(prefix="konsole-aware-tests-")
os.environ["XDG_CONFIG_HOME"] = os.path.join(_ROOT, "config")
os.environ["XDG_CACHE_HOME"] = os.path.join(_ROOT, "cache")
os.environ["KONSOLE_AWARE_STATE_DIR"] = os.path.join(_ROOT, "state")
os.environ["KONSOLE_AWARE_TERMINAL"] = "null"
os.environ["KONSOLE_AWARE_NO_DBUS"] = "1"
os.environ["KONSOLE_AWARE_SESSION_KEY"] = "test:/Sessions/1"
for var in ("CLAUDE_EFFORT", "KONSOLE_DBUS_SERVICE", "KONSOLE_DBUS_SESSION", "KONSOLE_DBUS_WINDOW"):
    os.environ.pop(var, None)
