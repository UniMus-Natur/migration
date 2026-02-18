import os
import sys

# Monkey patch MySQLdb with PyMySQL for local development
try:
    import pymysql
    pymysql.install_as_MySQLdb()
except ImportError:
    pass

import django

def setup():
    """
    Sets up the Django environment for Specify 7 using the submodule.
    Call this function at the start of your migration scripts.
    """
    # 1. Add current directory to sys.path (so we can import config)
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if BASE_DIR not in sys.path:
        sys.path.append(BASE_DIR)

    # 2. Add specify7 submodule to sys.path
    SPECIFY_DIR = os.path.join(BASE_DIR, 'specify7')
    if SPECIFY_DIR not in sys.path:
        sys.path.append(SPECIFY_DIR)

    # 3. Inject our local settings into the expected specifyweb module location
    # BUT only if we are NOT running in Kubernetes (where we want to use the mounted secrets)
    if os.environ.get('KUBERNETES_SERVICE_HOST'):
        print("Running in Kubernetes: Skipping injection of config.local_specify_settings (using mounted secrets)")
    else:
        try:
            import config.local_specify_settings
            sys.modules['specifyweb.settings.local_specify_settings'] = config.local_specify_settings
            # print(f"Successfully injected local settings from {config.local_specify_settings.__file__}")
        except ImportError:
            print("Warning: config.local_specify_settings not found. Using Specify defaults.")

    # 4. Configure Django settings
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "specifyweb.settings")

    # 5. Initialize Django
    try:
        django.setup()
        # print("Specify 7 Django environment initialized.")
    except Exception as e:
        print(f"Error initializing Django: {e}")
        raise

if __name__ == "__main__":
    setup()
