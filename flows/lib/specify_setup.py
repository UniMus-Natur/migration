"""Bootstrap the Specify 7 Django ORM for use inside Prefect flows.

Adds the repo root and the ``specify7`` submodule to ``sys.path``, injects
local settings when running outside Kubernetes, and calls ``django.setup()``.
"""

import os
import sys

_DJANGO_INITIALIZED = False


def setup_django() -> None:
    """Idempotent Django bootstrap – safe to call multiple times."""
    global _DJANGO_INITIALIZED
    if _DJANGO_INITIALIZED:
        return

    # pymysql shim (only needed when mysqlclient is not available)
    try:
        import pymysql
        pymysql.install_as_MySQLdb()
    except ImportError:
        pass

    base_dir = os.path.dirname(  # repo root
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    specify_dir = os.path.join(base_dir, "specify7")

    for path in (base_dir, specify_dir):
        if path not in sys.path:
            sys.path.append(path)

    if os.environ.get("KUBERNETES_SERVICE_HOST"):
        pass  # use mounted secrets
    else:
        try:
            import config.local_specify_settings
            sys.modules["specifyweb.settings.local_specify_settings"] = (
                config.local_specify_settings
            )
        except ImportError:
            pass

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "specifyweb.settings")

    import django
    django.setup()
    _DJANGO_INITIALIZED = True
