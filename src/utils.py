from pathlib import Path
import platform
import re
import sys
import types

from colorama import init as coloramaInit
coloramaInit()

# Functions decorated with this will have their stdout redirected to stderr, and their return value printed to outputFile (stdout if none supplied)
def print_return(f):
	def wrap(*, outputFile = sys.stdout, **kw):
		oldStdout, sys.stdout = sys.stdout, sys.stderr
		if isinstance(outputFile, str):
			with open(outputFile, 'w') as fp:
				return wrap(fp, *args, **kw)

		try:
			ret = f(**kw)
			if ret is not None:
				if isinstance(ret, list) or isinstance(ret, types.GeneratorType):
					ret = '\n'.join(ret)
				print(ret, file = outputFile)
		finally:
			sys.stdout = oldStdout
	return wrap

colorPattern = re.compile("\033\\[[0-9]+;[0-9]+m")
colors = {
	'black': 30,
	'red': 31,
	'green': 32,
	'yellow': 33,
	'blue': 34,
	'purple': 35,
	'cyan': 36,
	'white': 37,
	'clear': 0
}

#TODO
def useColors(str = None): return True

def clr(str, fg = None, bg = None, bold = True):
	a = clrSpecifier(fg, True, bold) if fg else '\033[1m' if bold else ''
	b = clrSpecifier(bg, False) if bg else ''
	c = clrSpecifier('clear')
	return a + b + str + c

def clrSpecifier(color, isForeground = True, bold = True):
	return "\033[%d;%dm" % (1 if bold else 0, colors[color] + (0 if isForeground else 10))

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
