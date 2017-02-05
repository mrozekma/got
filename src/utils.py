import sys
import re

from colorama import init as coloramaInit
coloramaInit()

# Functions decorated with this will have their stdout redirected to stderr, and their return value printed to stdout
def print_return(f):
	def wrap(*args, **kw):
		oldStdout, sys.stdout = sys.stdout, sys.stderr
		try:
			print(f(*args, **kw) or '', file = oldStdout)
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
