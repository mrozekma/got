import os
from pathlib import Path
import platform
import string
import sys
import types
from typing import *

gotRoot = os.environ.get('GOT_ROOT')
gotRoot = Path(gotRoot).resolve() if gotRoot is not None else (Path.home() / '.got')

class Template(string.Template):
	delimiter = '%'

# Functions decorated with this will have their stdout redirected to stderr, and their return value printed to outputFile (stdout if none supplied)
def print_return(f, onNone = None):
	def wrap(*, outputFile = sys.stdout, **kw):
		if isinstance(outputFile, str):
			with open(outputFile, 'w') as fp:
				return wrap(outputFile = fp, **kw)
		oldStdout, sys.stdout = sys.stdout, sys.stderr

		try:
			ret = f(**kw)
			if ret is None:
				if onNone:
					raise RuntimeError(onNone % kw)
			else:
				if isinstance(ret, list) or isinstance(ret, types.GeneratorType):
					for e in ret:
						if e is None:
							if onNone:
								raise RuntimeError(onNone % kw)
						else:
							print(e, file = outputFile, flush = True)
				else:
					print(ret, file = outputFile)
		finally:
			sys.stdout = oldStdout
	return wrap

def makeGitEnvironment(host: 'Host') -> Dict[str, str]:
	from .DB import gotRoot
	scriptExtension = '.bat' if platform.system() == 'Windows' else ''
	rtn = {
		'GIT_ASKPASS': str(Path(__file__).parent.parent / f"got-credential-helper{scriptExtension}"),
		'GIT_CONFIG_NOSYSTEM': 'true',
		'GOT_PYTHON': sys.executable,
		'GOT_SCRIPT': str(Path(__file__).parent.parent / 'got'),
		'GOT_HOSTNAME': host.name,
		'GOT_ROOT': str(gotRoot),
	}
	if host.ssh_key_path is not None:
		rtn['GIT_SSH_COMMAND'] = f'ssh -i "{host.ssh_key_path}"'
	return rtn


class VerboseBlock:
	def __init__(self, set):
		self.set = set

	def __enter__(self):
		global verbosity
		self.old, verbosity = verbosity, self.set

	def __exit__(self, exc_type, exc_val, exc_tb):
		global verbosity
		verbosity = self.old

verbosity = 1
def verbose(lvl: int = None, *, set: int = None, temp: int = None) -> Union[int, bool, VerboseBlock]:
	'''
	verbose() returns the current level
	verbose(lvl) returns True if the current level is at least 'lvl'
	verbose(set = lvl) sets the current level to 'lvl'
	with verbose(temp = lvl) sets the current level to 'lvl' until the with-block ends
	'''
	if set is not None:
		global verbosity
		verbosity = set
	if temp is not None:
		return VerboseBlock(temp)
	if lvl is not None:
		return verbosity >= lvl
	return verbosity
