import argparse
from collections import OrderedDict
from getpass import getpass
import git
import json
from lockfile import LockFile, LockTimeout
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from .Credentials import credentials
from .DB import db, gotRoot
from .Host import Host, BitbucketHost
from .RepoSpec import RepoSpec, HOST_PATTERN
from .utils import print_return, makeGitEnvironment

class DeprecatedAction(argparse.Action):
	def __init__(self, option_strings, dest, why = None, **kw):
		if 'nargs' not in kw:
			kw['nargs'] = 0
		if 'help' not in kw:
			kw['help'] = argparse.SUPPRESS
		super().__init__(option_strings, '__deprecated__', **kw)
		self.why = why

	def __call__(self, parser, namespace, values, option_string = None):
		if option_string:
			print(f"Warning: {option_string} is deprecated" + (f": {self.why}" if self.why else ''), file = sys.stderr)

parser = argparse.ArgumentParser(add_help = False)
parser.add_argument('-v', '--verbose', action = DeprecatedAction, why = 'verbose output is enabled by default')
parser.add_argument('-q', '--quiet', action = 'store_true', help = "don't output verbose information to stderr")
parser.add_argument('--unlock', action = 'store_true', help = 'remove the lockfile if it exists')
verbose = True

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

def type_multipart_repospec(spec):
	# '@file' means read the specs from 'file'
	if spec.startswith('@'):
		path = Path(spec[1:])
		if not path.exists():
			raise argparse.ArgumentTypeError(f"File not found: {path}")
		specs = path.read_text().strip().split('\n')
	# 'project/*' is a special case for Bitbucket hosts
	elif spec.endswith('/*'):
		project = spec[:-2]
		# If a host is specified, check that one; otherwise check all Bitbucket hosts
		repo = RepoSpec.fromStr(project)
		if repo.host:
			try:
				hosts = [Host.fromDB(repo.host)]
			except ValueError:
				raise argparse.ArgumentTypeError(f"Invalid multipart repospec: no host named `{repo.host}'")
			if not isinstance(hosts[0], BitbucketHost):
				raise argparse.ArgumentTypeError(f"Unable to resolve multipart repospec: host `{repo.host}' is not a Bitbucket host")
		else:
			hosts = [host for host in map(Host.fromDB, db.hosts.keys()) if isinstance(host, BitbucketHost)]
		specs = [f"{host.name}:{project}/{reponame}" for host in hosts for reponame in host.getReposInProject(project)]
	else:
		specs = [spec]
	return map(type_repospec, specs)

def findRepo(repospec):
	if not db.hosts.keys():
		if verbose:
			print("No hosts registered -- add one with --add-host")
		return None, None

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

def where(repo, format, on_uncloned, ensure_on_disk = True, dest = None):
	def formatRtn(repo, path):
		if format == 'plain':
			return path
		elif format == 'py':
			return {'repospec': repo, 'path': path}
		elif format == 'json':
			return json.dumps({'repospec': str(repo), 'path': path})

	if verbose:
		print(f"Looking up {repo}")

	# Ambiguous repospecs are a problem. If 'repo' lacks a host, and we can find exactly one matching clone, we use it
	candidates = [spec for spec in map(RepoSpec.fromStr, db.clones.keys()) if spec.name == repo.name and spec.revision == repo.revision and (repo.host is None or spec.host == repo.host)]
	if len(candidates) == 1:
		repo = candidates[0]
		localPath = db.clones[str(repo)]
		# Make sure the local path actually exists
		if not ensure_on_disk or Path(localPath).is_dir():
			return formatRtn(repo, localPath)
		elif verbose:
			print(f"Local clone `{localPath}' no longer exists")
	elif len(candidates) > 1:
		raise RuntimeError(f"Ambiguous repospec {repo} matches multiple clones: {', '.join(map(str, candidates))}")
	elif verbose:
		print("No local clone on record")

	if on_uncloned == 'skip':
		return
	elif on_uncloned == 'fail':
		raise RuntimeError(f"No local clone of {repo}")
	elif on_uncloned == 'fake':
		return str(Path(db.config['clone_root']) / '__REPO_NOT_FOUND__')

	# If we don't have a matching clone, we need to find its host and clone it
	host, url = findRepo(repo)
	if host is None:
		raise RuntimeError(f"Unable to resolve repospec {repo}")
	if repo.host is None:
		repo.host = host.name

	localPath = Path(dest) if dest else Path(db.config['clone_root']) / host.name / (f"{repo.name}@{repo.revision}" if repo.revision is not None else repo.name)
	if localPath.is_dir():
		if verbose:
			print(f"{localPath} already exists; switching to here mode")
		return here(repo, str(localPath), False)

	os.makedirs(localPath.parent, exist_ok = True)
	if verbose:
		print(f"Cloning {url} to {localPath}")
	git.Repo.clone_from(url, str(localPath), env = makeGitEnvironment(host.name))
	if repo.revision is not None:
		r = git.Repo(str(localPath))
		r.head.reference = r.commit(repo.revision)
		r.head.reset(index = True, working_tree = True)
	db.clones[str(repo)] = str(localPath)
	return formatRtn(repo, str(localPath))

# This is an adapter for command-line where mode. 'repos' comes from an argument of type 'multipart_repospec' with '+' nargs, so it's a list of lists of repospecs that needs to be flattened and passed to where() individually
def whereCLI(repos, format, on_uncloned, ensure_on_disk = True, dest = None):
	repos = [spec for l in repos for spec in l]
	if dest is not None and len(repos) > 1:
		raise ValueError("Can't specify a clone destination with multiple repospecs")
	return [where(repo, format, on_uncloned, ensure_on_disk, dest) for repo in repos]

def here(repo, dir, force):
	if dir == '-':
		existing = where(repo, 'py', 'skip', False)
		if existing:
			repo = existing['repospec']
			del db.clones[str(repo)]
			print(f"{repo} no longer has a registered local clone")
			if Path(existing['path']).exists():
				print(f"(old path still exists on disk: {existing['path']})")
		return

	if repo.host is None:
		raise ValueError(f"{repo} does not specify the host; it should be of the form <host>:{repo}")

	dir = Path(dir).resolve()
	if not force:
		existing = where(repo, 'py', 'skip')
		if existing:
			raise ValueError(f"{repo} is already mapped to {existing['path']}")
		cloneUrl = Host.fromDB(repo.host).getCloneURL(repo.name)

		if not dir.exists():
			raise ValueError(f"Path not found: {dir}")
		try:
			r = git.Repo(str(dir))
		except git.InvalidGitRepositoryError:
			raise ValueError(f"Path is not a git repository: {dir}")
		try:
			if not cloneUrl in r.remotes['origin'].urls:
				raise ValueError(f"Repository does not have the correct origin URL: {cloneUrl}")
		except IndexError:
			raise ValueError(f"Repository has no origin: {dir}")

	db.clones[str(repo)] = str(dir)
	print(f"{repo} is located at {dir}")

def what(dir):
	path = (Path(dir) if dir is not None else Path.cwd()).resolve()
	for k, v in db.clones.items():
		if Path(v).resolve() == path:
			return k

def whence(repo, format):
	host, url = findRepo(repo)
	if format == 'plain':
		if url is not None:
			return url
	elif format == 'json':
		return json.dumps({'repospec': str(repo), 'host': host.name, 'url': url})

def showHosts(format):
	if format == 'plain':
		print(f"    {'Name':30} {'Type':20} URL")
		for name, host in sorted(db.hosts.items()):
			try:
				Host.fromDB(name)
				valid = True
			except Exception:
				valid = False
			print(f"{'   ' if valid else '(!)'} {name:30} {host['type']:20} {host['url']}")
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
	print(f"Added {type} host {name} at {url}")

def editHost(name, new_url, new_username, new_password, force):
	if not name in db.hosts:
		raise ValueError(f"No host named {name}")
	print(f"Editing host: {name}")

	if new_password == '-':
		new_password = getpass()

	# Mutable fields
	fields = [
		('url', db.hosts[name]['url'], new_url),
		('username', credentials[name][0], new_username),
		('password', credentials[name][1], new_password),
	]
	# Immutable fields
	kw = {
		'name': name,
		'type': db.hosts[name]['type'],
	}

	for (field, oldVal, newVal) in fields:
		if newVal:
			kw[field] = newVal
			print(f"  New {field}: {'***' if field == 'password' else newVal}")
			if field == 'url':
				print("    Warning: Existing clones will still point to the old remote URL")
		else:
			kw[field] = oldVal

	try:
		Host(**kw)
	except ConnectionError as e:
		if force:
			print(f"Host error (editing anyway): {e}")
		else:
			raise ConnectionError(f"Unable to edit host: {e}")

	db.hosts[kw['name']] = {'type': kw['type'], 'url': kw['url']}
	credentials[kw['name']] = kw['username'], kw['password']

def rmHost(name):
	if name not in db.hosts:
		raise RuntimeError(f"Unknown host `{name}'")
	del db.hosts[name]
	del credentials[name]
	print(f"Removed host {name}")
	clones = [spec for spec in db.clones.keys() if RepoSpec.fromStr(spec).host == name]
	if clones:
		for spec in clones:
			del db.clones[spec]
		print(f"Unregistered {len(clones)} {'clone' if len(clones) == 1 else 'clones'}")

def iterDeps(spec):
	if spec is None:
		spec = what(None)
		if spec is None:
			print("Current directory is not a tracked repository")
			return

	seen = set()
	worklist = [spec]
	while worklist:
		spec = worklist.pop(0)
		repo = RepoSpec.fromStr(spec)
		w = where(repo, 'py', 'clone')
		seen.add(spec)
		yield w['repospec'], w['path']

		depsPath = Path(w['path']) / 'deps.got'
		if depsPath.exists():
			worklist += [depSpec for depSpec in depsPath.read_text().split() if depSpec not in seen]
		elif len(seen) == 1: # This is the first repo, the one the user specified
			print(f"{repo} has no dependencies file ({depsPath})")

def deps(repo):
	return list(str(depRepo) for depRepo, w in iterDeps(None if repo is None else str(repo)))

def gitPassthrough(directory, ignore_errors, args):
	if not args:
		raise ValueError("No git command specified")
	command, args = args[0], args[1:]
	# The treatment of version-pinned repos varies by command
	if command in ('commit', 'push'):
		pinnedBehavior = 'skip'
	elif command in ('fetch', 'pull'):
		pinnedBehavior = 'reset'
	else:
		pinnedBehavior = 'normal'

	# Figure out the root repo
	rootRepo = what(directory)
	if rootRepo is None:
		raise RuntimeError(f"Not a got repository: {Path(directory).resolve()}")

	# Iterate over the root repo and its dependencies
	failed = 0
	for spec, w in iterDeps(rootRepo):
		repo = git.Repo(w)
		if spec.revision and repo.index.diff(None):
			print(f"{spec}: Unexpected changes in version-pinned repository")
		elif spec.revision and repo.commit(spec.revision) != repo.head.commit:
			print(f"{spec}: Wrong HEAD in version-pinned repository")
		else:
			print(spec)
		with repo.git.custom_environment(**makeGitEnvironment(spec.host)):
			try:
				if spec.revision:
					if pinnedBehavior == 'skip':
						continue
					elif pinnedBehavior == 'reset':
						repo.remotes['origin'].fetch()
						repo.head.reset(spec.revision, hard = True)
						continue
				print(getattr(repo.git, command)(*args))
			except git.exc.GitCommandError as e:
				if ignore_errors:
					failed += 1
					print(f"Ignored error: {e}")
				else:
					raise e
			print()

	if failed:
		print(f"Command failed on {failed} {'repository' if failed == 1 else 'repositories'}")

def config(key, value):
	if key is None:
		for key, value in db.config.items():
			print(f"{key} = {value}")
	else:
		if key not in db.config:
			raise ValueError(f"Configuration key not found: {key}")
		if value is None:
			print(db.config[key])
		else:
			print(f"Old value: {db.config[key]}")
			db.config[key] = value
			print(f"New value: {db.config[key]}")

def mv(repospec, dest):
	clone = where(repospec, 'py', 'skip')
	if clone is None:
		raise ValueError(f"No clone found for {repospec}") from None
	repospec, src = clone['repospec'], clone['path']
	dest = Path(dest).resolve()
	if dest.exists():
		if not dest.is_dir():
			raise ValueError(f"Destination already exists: {dest}")
		dest /= os.path.basename(dest)
		if dest.exists():
			raise ValueError(f"Destination already exists: {dest}")
	shutil.move(src, dest)
	db.clones[str(repospec)] = str(dest)
	print(f"{repospec} moved to {dest}")

def findRoot(dir):
	dirs = db.clones.values()
	path = (Path(dir) if dir is not None else Path.cwd()).resolve()
	for candidate in [str(path), *map(str, path.parents)]:
		if candidate in dirs:
			return candidate
	raise RuntimeError(f"`{path}' is not within a got repository")

def getCredential(host):
	if host not in credentials:
		raise ValueError(f"Unrecognized host: {host}")
	username, password = credentials[host]
	print(password)

whereParser = makeMode('where', print_return(whereCLI), 'find the local path to a package, cloning it from a git host if necessary', ['local'])
whereParser.add_argument('repos', nargs = '+', type = type_multipart_repospec)
whereParser.add_argument('--format', choices = ['plain', 'json'], default = 'plain')
whereParser.add_argument('-o', '--output-file', dest = 'outputFile', metavar = 'FILE', default = sys.stdout, help = 'optional file to hold the results rather than stdout')
whereParser.add_argument('--outputFile', help = argparse.SUPPRESS) # backwards-compatible version of --output-file
group = whereParser.add_mutually_exclusive_group()
group.add_argument('--on-uncloned', choices = ['clone', 'skip', 'fail', 'fake'], default = 'clone', help = "what to do if the clone doesn't exist")
group.add_argument('--no-clone', action = 'store_const', dest = 'on_uncloned', const = 'skip', help = argparse.SUPPRESS) # backwards-compatibility version of --on-uncloned=skip
whereParser.add_argument('-d', '--dest', nargs = '?', default = None, help = 'where to store a new clone if one is made')

hereParser = makeMode('here', here, 'set the local path of a package')
hereParser.add_argument('repo', type = type_repospec)
hereParser.add_argument('dir', nargs = '?', default = '.', help = 'local path to set, or - to clear')
hereParser.add_argument('-f', '--force', action = 'store_true', help = 'register the path even if a record exists or the specified directory is invalid')

whatParser = makeMode('what', print_return(what), 'find the package name of a local clone')
whatParser.add_argument('dir', nargs = '?', default = None, help = 'directory to lookup')

whenceParser = makeMode('whence', print_return(whence), 'find the remote git path for a package', ['remote'])
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

editHostParser = makeMode('edit-host', editHost, 'edit a registered git host')
editHostParser.add_argument('name', type = type_host_name)
editHostParser.add_argument('--new-url', metavar = 'URL')
editHostParser.add_argument('--new-username', metavar = 'USERNAME')
editHostParser.add_argument('--new-password', nargs = '?', const = '-', metavar = 'PASSWORD')
editHostParser.add_argument('--force', action = 'store_true')

rmHostParser = makeMode('rm-host', rmHost, 'remove a registered git host')
rmHostParser.add_argument('name', type = type_host_name)

depsParser = makeMode('deps', print_return(deps), 'list local paths of all dependencies')
depsParser.add_argument('repo', nargs = '?', type = type_repospec, default = None)

gitParser = makeMode('git', gitPassthrough, 'run a git command on the repo and all its dependencies')
gitParser.add_argument('-C', '--directory', metavar = 'DIR', default = '.', help = 'root directory')
gitParser.add_argument('-i', '--ignore-errors', action = 'store_true', help = "don't stop if the git command fails")
gitParser.add_argument('args', nargs = argparse.REMAINDER, help = 'arguments to pass to git')

configParser = makeMode('config', config, 'get/set configuration key(s)')
configParser.add_argument('key', nargs = '?', help = 'configuration key to get/set; if omitted, all keys are shown')
configParser.add_argument('value', nargs = '?', help = 'value to set')

mvParser = makeMode('mv', mv, 'move a cloned repository on disk')
mvParser.add_argument('repospec', type = type_repospec)
mvParser.add_argument('dest')

findRootParser = makeMode('find-root', print_return(findRoot), 'find the root of a clone given a path within it')
findRootParser.add_argument('dir', nargs = '?', default = None, help = 'directory to start from')

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
verbose = not (args.quiet or ('GOT_QUIET' in os.environ))

# Then use the mode-specific parser to do the real parse
modeArgs = args.modeParser.parse_args(extraArgs)

# Special case the lock for --get-credentials; it needs to run while got is already locked
lockBase = 'creds-lock' if modeArgs.handler == getCredential else 'lock'
lock = LockFile(str(gotRoot / lockBase))
lockTimeout = False

# And pass those args to the mode's handler (don't pass 'handler', it's not a real argument)
if args.unlock:
	lock.break_lock()
while not lock.i_am_locking():
	try:
		lock.acquire(3)
	except LockTimeout:
		if not lockTimeout:
			lockTimeout = True
			if verbose:
				print("Waiting for lock... (pass --unlock if the lock is stale)", file = sys.stderr)

db.load()
try:
	modeArgs.handler(**{k: v for k, v in vars(modeArgs).items() if k != 'handler'})
finally:
	lock.release()
