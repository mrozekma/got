from pathlib import Path
import platform
import re
import sys
import types

# Functions decorated with this will have their stdout redirected to stderr, and their return value printed to outputFile (stdout if none supplied)
def print_return(f):
	def wrap(*, outputFile = sys.stdout, **kw):
		if isinstance(outputFile, str):
			with open(outputFile, 'w') as fp:
				return wrap(outputFile = fp, **kw)
		oldStdout, sys.stdout = sys.stdout, sys.stderr

		try:
			ret = f(**kw)
			if isinstance(ret, list) or isinstance(ret, types.GeneratorType):
				ret = '\n'.join(e for e in ret if e)
			print(ret, file = outputFile)
		finally:
			sys.stdout = oldStdout
	return wrap

def makeGitEnvironment(hostname):
	from .DB import gotRoot
	scriptExtension = '.bat' if platform.system() == 'Windows' else ''
	return {
		'GIT_ASKPASS': str(Path(__file__).parent.parent / f"got-credential-helper{scriptExtension}"),
		'GIT_CONFIG_NOSYSTEM': 'true',
		'GOT_PYTHON': sys.executable,
		'GOT_SCRIPT': str(Path(__file__).parent.parent / 'got'),
		'GOT_HOSTNAME': hostname,
		'GOT_ROOT': str(gotRoot),
	}
