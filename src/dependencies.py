# Try importing everything from the requirements file to make sure it's installed.

# Some projects have a different name from the package they contain; in those cases we have a comment in the requirements file containing the actual package name

from importlib import import_module
from pathlib import Path
import re
import sys

requirementsPath = Path(__file__).parent.parent / 'requirements.txt'
pattern = re.compile('^([a-zA-Z0-9]+).*?(?: # ([a-zA-Z0-9]+))?$')
for line in requirementsPath.read_text().split('\n'):
	match = pattern.match(line)
	if match:
		project, package = match.groups()
		try:
			import_module(package or project)
		except ImportError:
			raise RuntimeError(f"Missing Python dependencies. Run the following to install: {sys.executable} -m pip install -r {requirementsPath}")
