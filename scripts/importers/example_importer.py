import sys
import os

# Ensure we can find bootstrap.py from subdirectories
# Add the parent directory (scripts/) to sys.path
current_dir = os.path.dirname(os.path.abspath(__file__))
scripts_dir = os.path.dirname(current_dir)
if scripts_dir not in sys.path:
    sys.path.append(scripts_dir)

import bootstrap

# Initialize Django environment only if run as main script
if __name__ == "__main__":
    bootstrap.setup()

# Now you can import models
from specifyweb.specify.models import Collection, Accession

def run():
    print("Running example importer...")
    try:
        count = Collection.objects.count()
        print(f"Found {count} collections in the database.")
    except Exception as e:
        print(f"Error querying database: {e}")
        print("Make sure you have configured config/local_specify_settings.py correctly.")

if __name__ == "__main__":
    run()
