# At the project root so pytest puts this folder on sys.path (`import app` works) and runs it before the tests.
import os

os.environ["API_KEY"] = "test-key"
