"""launch.py — starts the Flask dev server and opens the browser."""
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

BASE_DIR = Path(__file__).parent
PYTHON = BASE_DIR / 'venv' / 'Scripts' / 'python.exe'
APP = BASE_DIR / 'app.py'
URL = 'http://127.0.0.1:5000'

if not PYTHON.exists():
    PYTHON = Path(sys.executable)

proc = subprocess.Popen([str(PYTHON), str(APP)], cwd=str(BASE_DIR))

time.sleep(2)
webbrowser.open(URL)

try:
    proc.wait()
except KeyboardInterrupt:
    proc.terminate()
