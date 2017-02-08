import argparse
from collections import OrderedDict
from getpass import getpass
import git
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys

from .Credentials import credentials
from .DB import db
from .Host import Host
from .RepoSpec import RepoSpec, HOST_PATTERN
from .utils import clr, print_return

CLONE_ROOT = Path.home() / '.got' / 'repos'
os.makedirs(CLONE_ROOT, exist_ok = True)

parser = argparse.ArgumentParser(add_help = False)
parser.add_argument('-v', '--verbose', action = 'store_true')
verbose = False

modeGroup = parser.add_mutually_exclusive_group()
def makeMode(name, handler, desc, aliases = []):
	rtn = argparse.ArgumentParser(prog = f"{parser.prog} --{name}")
	rtn.set_defaults(handler = handler)
	modeGroup.add_argument(f"--{name}", action = 'store_const', dest = 'modeParser', const = rtn, help = desc or f"{name.title()} mode")
	for alias in aliases:
		modeGroup.add_argument(f"--{alias}", action = 'store_const', dest = 'modeParser', const = rtn, help = argparse.SUPPRESS)
	return rtn

def type_host_name(name):
	if re.match(HOST_PATTERN, name) is None:
		raise argparse.ArgumentTypeError(f"Invalid remote name: {name}")
	return name

def type_repospec(spec):
	try:
		return RepoSpec.fromStr(spec)
	except ValueError as e:
		raise argparse.ArgumentTypeError(str(e))

def findRepo(repospec):
	# If the repospec specifies a host, check that one; otherwise check them all
	hosts = [repospec.host] if repospec.host else db.hosts.keys()
	for hostname in hosts:
		try:
			host = Host.fromDB(hostname)
			return host, host.getCloneURL(repospec.name)
		except Exception as e:
			if verbose:
				print(f"{hostname}: {e}")
	if verbose:
		print("No valid host has a record of the requested repository")
	return None, None

@print_return
def where(repo, format, clone):
	def formatRtn(repo, url):
		if format == 'plain':
			return url
		elif format == 'json':
			return json.dumps({'repospec': str(repo), 'url': url})

	# Ambiguous repospecs are a problem. If 'repo' lacks a host, and we can find exactly one matching clone, we use it
	candidates = [spec for spec in map(RepoSpec.fromStr, db.clones.keys()) if spec.name == repo.name and spec.revision == repo.revision and (repo.host is None or spec.host == repo.host)]
	if len(candidates) == 1:
		repo = candidates[0]
		localPath = db.clones[str(repo)]
		# Make sure the local path actually exists
		if Path(localPath).is_dir():
			return formatRtn(repo, localPath)
		elif verbose:
			print(f"Local clone `{localPath}' no longer exists")
	elif len(candidates) > 1:
		raise RuntimeError(f"Ambiguous repospec {repo} matches multiple clones: {', '.join(map(str, candidates))}")
	elif verbose:
		print("No local clone on record")

	# If we don't have a matching clone, we need to find its host and clone it
	host, url = findRepo(repo)
	if host is None:
		raise RuntimeError(f"Unable to resolve repospec {repo}")

	localPath = CLONE_ROOT / host.name / (f"{repo.name}@{repo.revision}" if repo.revision is not None else repo.name)
	os.makedirs(localPath.parent, exist_ok = True)
	if verbose:
		print(f"Cloning {url} to {localPath}")
	scriptExtension = '.bat' if platform.system() == 'Windows' else ''
	git.Repo.clone_from(url, str(localPath), env = {
		'GIT_ASKPASS': str(Path(__file__).parent.parent / f"got-credential-helper{scriptExtension}"),
		'GOT_PYTHON': sys.executable,
		'GOT_SCRIPT': str(Path(__file__).parent.parent / 'got'),
		'GOT_HOSTNAME': host.name,
	})
	db.clones[str(repo)] = str(localPath)
	return str(localPath)

@print_return
def whence(repo, format):
	host, url = findRepo(repo)
	if format == 'plain':
		if url is not None:
			return url
	elif format == 'json':
		return json.dumps({'repospec': str(repo), 'host': host.name, 'url': url})

def showHosts(format):
	if format == 'plain':
		print(clr(f"{'Name':30} {'Type':20} URL", bold = True))
		for name, host in sorted(db.hosts.items()):
			print(f"{name:30} {host['type']:20} {host['url']}")
	elif format == 'json':
		print(db.hosts.getJSON())

def addHost(name, url, type, username, password, force):
	if name in db.hosts:
		raise RuntimeError(f"Unable to add host: name `{name}' already mapped to {db.hosts[name]['url']}")
	if password == '-':
		password = getpass()

	# Make sure the host is valid
	try:
		Host(name, type, url, username, password)
	except ConnectionError as e:
		if force:
			print(f"Host error (continuing anyway): {e}")
		else:
			raise ConnectionError(f"Unable to add host: {e}")

	# Save
	db.hosts[name] = {'type': type, 'url': url}
	credentials[name] = username, password

def rmHost(name):
	if name not in db.hosts:
		raise RuntimeError(f"Unknown host `{name}'")
	del db.hosts[name]

def getCredential(host):
	if host not in credentials:
		raise ValueError(f"Unrecognized host: {host}")
	username, password = credentials[host]
	print(password)

whereParser = makeMode('where', where, 'find the local path to a package, cloning it from a git host if necessary', ['local'])
whereParser.add_argument('repo', type = type_repospec)
whereParser.add_argument('--format', choices = ['plain', 'json'], default = 'plain')
whereParser.add_argument('--no-clone', action = 'store_false', dest = 'clone', default = True, help = 'fail if the repository is not already cloned on disk')

whenceParser = makeMode('whence', whence, 'find the remote git path for a package', ['remote'])
whenceParser.add_argument('repo', type = type_repospec)
whenceParser.add_argument('--format', choices = ['plain', 'json'], default = 'plain')

hostsParser = makeMode('hosts', showHosts, 'list all registered git hosts')
hostsParser.add_argument('--format', choices = ['plain', 'json'], default = 'plain')

addHostParser = makeMode('add-host', addHost, 'add a new git host')
addHostParser.add_argument('name', type = type_host_name)
addHostParser.add_argument('url')
addHostParser.add_argument('-t', '--type', choices = ['bitbucket', 'daemon'], default = 'bitbucket', help = 'git host type')
addHostParser.add_argument('-u', '--username', default = '', help = 'login username')
addHostParser.add_argument('-p', '--password', nargs = '?', default = '', const = '-', help = "login password (empty or '-' to prompt)")
addHostParser.add_argument('--force', action = 'store_true', help = 'add the host even if a connection cannot be established')

rmHostParser = makeMode('rm-host', rmHost, 'remove a registered git host')
rmHostParser.add_argument('name', type = type_host_name)

# This is used by git-credential, it's not meant for direct user interaction
getCredentialParser = makeMode('get-credential', getCredential, argparse.SUPPRESS)
getCredentialParser.add_argument('host')

# Running with no arguments (or with just -h/--help) will silently pick --where and then give you the help output for that mode, which is confusing. Print the general help instead
if len(sys.argv) == 1 or (len(sys.argv) == 2 and sys.argv[1] in ('-h', '--help')):
	parser.print_help()
	exit(0)

parser.set_defaults(modeParser = whereParser)
# First parse to isolate the mode; we get back a namespace containing 'modeParser' for the mode-specific parser, and a list of all the unprocessed arguments to pass on
args, extraArgs = parser.parse_known_args()
verbose = args.verbose
# Then use the mode-specific parser to do the real parse
args = args.modeParser.parse_args(extraArgs)
# And pass those args to the mode's handler (don't pass 'handler', it's not a real argument)
args.handler(**{k: v for k, v in vars(args).items() if k != 'handler'})
