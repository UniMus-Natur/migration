
import bootstrap
bootstrap.setup()

try:
    from specifyweb.specify.models import Collection, Accession
    print("SUCCESS: Successfully imported Specify models!")
    print(f"Collection model: {Collection}")
    print(f"Accession model: {Accession}")
except ImportError as e:
    print(f"FAILURE: Could not import models. Error: {e}")
except Exception as e:
    print(f"FAILURE: An unexpected error occurred: {e}")
