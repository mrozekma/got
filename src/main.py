import argparse
from getpass import getpass
import git
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time

from .DB import db, Like
from .Credential import Credential
from .Config import config
from .Clone import Clone
from .Host import Host

from .RepoSpec import RepoSpec, HOST_PATTERN
from .utils import print_return, makeGitEnvironment, verbose, Template

# Type hints
from typing import *
URL = NewType('URL', str)
JSON = NewType('JSON', str)

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
parser.add_argument('--unlock', action = DeprecatedAction, help = 'stale locks are now detected automatically')
# --verbose and --quiet are handled by the root script; they're included here so they show up in help output and don't cause argparse errors when present
parser.add_argument('-v', '--verbose', action = 'count', default = None, help = 'increase the verbosity level')
parser.add_argument('-q', '--quiet', action = 'store_const', dest = 'verbose', const = 0, help = "don't output verbose information to stderr (clear the verbosity level)")

modeGroup = parser.add_mutually_exclusive_group()
def makeMode(name: str, handler: Callable, desc: Optional[str], aliases: List[str] = []) -> argparse.ArgumentParser:
	rtn = argparse.ArgumentParser(prog = f"{parser.prog} --{name}")
	rtn.set_defaults(handler = handler)
	modeGroup.add_argument(f"--{name}", action = 'store_const', dest = 'modeParser', const = rtn, help = desc or f"{name.title()} mode")
	for alias in aliases:
		modeGroup.add_argument(f"--{alias}", action = 'store_const', dest = 'modeParser', const = rtn, help = argparse.SUPPRESS)
	return rtn

def type_host_name(name: str) -> str:
	if re.match(HOST_PATTERN, name) is None:
		raise argparse.ArgumentTypeError(f"Invalid remote name: {name}")
	return name

def type_repospec(spec: str) -> RepoSpec:
	try:
		return RepoSpec.fromStr(spec)
	except ValueError as e:
		raise argparse.ArgumentTypeError(str(e))

def type_multipart_repospec(spec: str) -> Iterable[RepoSpec]:
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
				hosts = [Host.load(name = repo.host, type = 'bitbucket')]
			except ValueError:
				raise argparse.ArgumentTypeError(f"Invalid multipart repospec: no Bitbucket host named `{repo.host}'")
		else:
			hosts = Host.loadAll(type = 'bitbucket')
		specs = [f"{host.name}:{project}/{reponame}" for host in hosts for reponame in host.getReposInProject(project)]
	else:
		specs = [spec]
	return map(type_repospec, specs)

def findRepo(repospec: RepoSpec) -> Tuple[Optional[Host], Optional[URL]]:
	if not Host.count():
		if verbose(1):
			print("No hosts registered -- add one with --add-host")
		return None, None

	# If the repospec specifies a host, check that one; otherwise check them all
	hosts = [Host.load(name = repospec.host)] if repospec.host else Host.loadAll()
	errors = []
	for host in hosts:
		try:
			return host, host.getCloneURL(repospec.name)
		except Exception as e:
			errors.append(f"{host.name}: {e}")
	if verbose(1):
		print()
		print("No valid host has a record of the requested repository:")
		for error in errors:
			print(f"  {error}")
	return None, None

def where(repo: RepoSpec, format: str, on_uncloned: str, ensure_on_disk: bool = True, dest: str = None) -> Optional[Union[str, Clone, JSON]]:
	# format: plain, py, json
	# on_uncloned: clone, skip, fail, fake
	def formatRtn(clone: Clone) -> Union[str, Clone, JSON]:
		if format == 'plain':
			return str(clone.path)
		elif format == 'py':
			return clone
		elif format == 'json':
			return json.dumps({'repospec': str(clone.repospec), 'path': str(clone.path)})

	def lookupRepo():
		# Ambiguous repospecs are a problem. If 'repo' lacks a host, and we can find exactly one matching clone, we use it
		candidates = list(Clone.loadSpec(repo))
		if len(candidates) == 1:
			clone = candidates[0]
			# Make sure the local path actually exists
			if not ensure_on_disk or clone.path.is_dir():
				return formatRtn(clone)
			elif verbose(1):
				print(f"{repo}: local clone `{clone.path}' no longer exists")
		elif len(candidates) > 1:
			raise RuntimeError(f"{repo}: Ambiguous repospec matches multiple clones: {', '.join(clone.repospec for clone in candidates)}")
		elif verbose(1):
			print(f"{repo}: no local clone on record")

	rtn = lookupRepo()
	if rtn is not None:
		return rtn

	if on_uncloned == 'skip':
		return
	elif on_uncloned == 'fail':
		raise RuntimeError(f"No local clone of {repo}")
	elif on_uncloned == 'fake':
		return formatRtn(Clone(repo, Path(config.clone_root) / '__REPO_NOT_FOUND__'))

	# If we don't have a matching clone, we need to find its host and clone it
	host, url = findRepo(repo)
	if host is None:
		raise RuntimeError(f"Unable to resolve repospec {repo}")
	if repo.host is None:
		repo.host = host.name

	with repo.lock():
		# We intentionally delay locking until after confirming the repo doesn't exist, but now that we're in the critical section we need to check again
		rtn = lookupRepo()
		if rtn is not None:
			return rtn

		localPath = Path(dest) if dest else Path(config.clone_root) / host.name / (f"{repo.name}@{repo.revision}" if repo.revision is not None else repo.name)
		if localPath.is_dir():
			if verbose(1):
				print(f"{localPath} already exists; switching to here mode")
			clone = here(repo, str(localPath), False)
			return formatRtn(clone)

		os.makedirs(localPath.parent, exist_ok = True)
		if verbose(1):
			print(f"Cloning {url} to {localPath}")
		git.Repo.clone_from(url, str(localPath), env = makeGitEnvironment(host))
		if repo.revision is not None:
			r = git.Repo(str(localPath))
			r.head.reference = r.commit(repo.revision)
			r.head.reset(index = True, working_tree = True)

		clone = Clone(repo, localPath)
		clone.save()
		return formatRtn(clone)

# This is an adapter for command-line where mode. 'repos' comes from an argument of type 'multipart_repospec' with '+' nargs, so it's a list of lists of repospecs that needs to be flattened and passed to where() individually
def whereCLI(repos: List[List[RepoSpec]], format: str, on_uncloned: str, ensure_on_disk: bool = True, dest: bool = None):
	repos = [spec for l in repos for spec in l]
	if dest is not None and len(repos) > 1:
		raise ValueError("Can't specify a clone destination with multiple repospecs")
	rtn = [where(repo, format, on_uncloned, ensure_on_disk, dest) for repo in repos]
	if format == 'json': # Convert the list of JSON strings into a JSON list
		rtn = json.dumps([json.loads(e) for e in rtn])
	return rtn

def here(repo: RepoSpec, dir: str, force: bool) -> Optional[Clone]:
	with repo.lock():
		if dir == '-':
			existing: Clone = where(repo, 'py', 'skip', False)
			if existing:
				existing.delete()
				print(f"{existing.repospec} no longer has a registered local clone")
				if existing.path.exists():
					print(f"(old path still exists on disk: {existing.path})")
			return

		if repo.host is None:
			raise ValueError(f"{repo} does not specify the host; it should be of the form <host>:{repo}")

		dir = Path(dir).resolve()
		if not force:
			existing: Clone = where(repo, 'py', 'skip')
			if existing:
				raise ValueError(f"{repo} is already mapped to {existing.path}")
			cloneUrl = Host.load(name = repo.host).getCloneURL(repo.name)

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

		rtn = Clone(repo, dir)
		rtn.save()
		print(f"{repo} is located at {dir}")
		return rtn

def what(dir: Optional[str]) -> Optional[RepoSpec]:
	path = findRoot(dir)
	if path is None:
		d = Path(dir) if dir is not None else Path.cwd()
		raise RuntimeError(f"Not a got repository: {d.resolve()}")

	# If any clone has this exact path already, return it
	clone = Clone.load(path = str(path))
	if clone is not None:
		return clone.repospec

	# If not, try resolving each path to find a match
	for clone in Clone.loadAll():
		if clone.path.resolve() == path:
			return clone.repospec

	# Shouldn't be able to get here
	d = Path(dir) if dir is not None else Path.cwd()
	raise RuntimeError(f"Not a got repository: {d.resolve()}")

def whence(repo: RepoSpec, format: str) -> Union[URL, JSON]:
	host, url = findRepo(repo)
	if host is None:
		raise RuntimeError(f"Unable to resolve repospec {repo}")
	if format == 'plain':
		if url is not None:
			return url
	elif format == 'json':
		return json.dumps({'repospec': str(repo), 'host': host.name, 'url': url})

def showHosts(format: str) -> None:
	if format == 'plain':
		print(f"    {'Name':30} {'Type':20} URL")
		for host in Host.loadAll(sort = 'name ASC'):
			try:
				host.check()
				valid = True
			except:
				valid = False
			print(f"{'   ' if valid else '(!)'} {host.name:30} {host.type:20} {host.url}")
	elif format == 'json':
		print(json.dumps({host.name: {'type': host.type, 'url': host.url} for host in Host.loadAll()}))

def addHost(name: str, url: str, type: str, username: str, password: str, ssh_key: Optional[str], clone_url: Optional[str], force: bool) -> None:
	host = Host(name, type, url, username, ssh_key, clone_url)
	with host.lock():
		existingHost = Host.tryLoad(name = name)
		if existingHost is not None:
			raise RuntimeError(f"Unable to add host: name `{name}' already mapped to {existingHost.url}")
		if password == '-':
			password = getpass()

		with db.transaction():
			if password is not None:
				cred = Credential(name, username, password)
				cred.save()
			# Make sure the host is valid
			try:
				host.check()
			except ConnectionError as e:
				if force:
					print(f"Host error (continuing anyway): {e}")
				else:
					raise ConnectionError(f"Unable to add host: {e}")

			# Save
			host.save()
	print(f"Added {type} host {name} at {url}")

def editHost(name: str, new_url: Optional[str], new_username: Optional[str], new_password: Optional[str], new_ssh_key: Optional[str], new_clone_url: Optional[str], update_clones: bool, force: bool) -> None:
	host = Host.load(name = name, err = f"No host named {name}")
	print(f"Editing host: {name}")

	if new_password == '-':
		new_password = getpass()

	with host.lock():
		with db.transaction():
			if new_url is not None:
				host.url = new_url
				print(f"  New URL: {new_url}")
			if new_username is not None:
				# The username is stored in both the hosts table and the keyring, so we need to delete the credential and make a new one, but first we need to pull the password out of the keyring so we can put it back with the new username
				cred = host.getCredential()
				if cred is not None:
					cred.delete()
					Credential(host.name, new_username, cred.password).save()
				host.username = new_username
				print(f"  New username: {new_username}")
			if new_password is not None:
				cred = host.getCredential()
				if cred is None:
					if new_password:
						Credential(host.name, host.username, new_password).save()
				elif new_password:
					cred.password = new_password
					cred.save()
				else:
					cred.delete()
				print(f"  New password: {'***' if new_password else '(none)'}")
			if new_ssh_key is not None:
				host.ssh_key_path = new_ssh_key
				print(f"  New SSH key: {new_ssh_key}")
			if new_clone_url is not None:
				host.clone_url = new_clone_url
				print(f"  New Clone URL: {new_clone_url}")

			try:
				host.check()
			except ConnectionError as e:
				if force:
					print(f"Host error (editing anyway): {e}")
				else:
					raise ConnectionError(f"Unable to edit host: {e}")

			if update_clones and ((new_url is not None) or (new_clone_url is not None)):
				count = 0
				print("Updating clones:")
				for clone in Clone.loadAll(repospec = Like(f"{name}:%")):
					try:
						r = git.Repo(str(clone.path))
					except git.exc.NoSuchPathError:
						print(f"  {clone.repospec}: local clone not found")
						continue
					url = host.getCloneURL(clone.repospec.str(False, False))
					r.remotes['origin'].set_url(url)
					print(f"  {clone.repospec}: {url}")
					count += 1
				print(f"Updated {count} clone remote {'URL' if count == 1 else 'URLs'}")
			host.save()

def rmHost(name: str) -> None:
	with db.transaction():
		host = Host.load(name = name, err = f"No host named {name}")
		cred = host.getCredential()
		if cred is not None:
			cred.delete()
		host.delete()
		num = Clone.deleteAll(repospec = Like(name.replace('\\', '\\\\').replace('%', '\\%') + ':%'))
		print(f"Removed host {name}")
		print(f"Unregistered {num} {'clone' if num == 1 else 'clones'}")

#TODO Change 'spec' to an Optional[RepoSpec]
def iterDeps(spec: Optional[str]) -> Iterable[Clone]:
	if spec is None:
		try:
			spec = str(what(None))
		except RuntimeError:
			print("Current directory is not a tracked repository")
			return

	seen = set()
	worklist = [spec]
	while worklist:
		spec = worklist.pop(0)
		if spec in seen:
			continue
		repo = RepoSpec.fromStr(spec)
		clone: Clone = where(repo, 'py', 'clone')
		seen.add(spec)
		yield clone

		depsPath = Path(clone.path) / 'deps.got'
		if depsPath.exists():
			worklist += [depSpec for depSpec in depsPath.read_text().split() if depSpec not in seen]
		elif len(seen) == 1: # This is the first repo, the one the user specified
			print(f"{repo} has no dependencies file ({depsPath})")

def deps(repo: Optional[RepoSpec], format: str) -> Iterable[str]:
	t = Template(format)
	for clone in iterDeps(None if repo is None else str(repo)):
		try:
			hexsha = git.Repo(str(clone.path)).head.commit.hexsha
		except:
			hexsha = '0' * 40
		try:
			yield t.substitute(
				H = hexsha,
				h = hexsha[:7],
				RS = clone.repospec.str(),
				rs = clone.repospec.str(False, False),
				p = str(clone.path),
			)
		except KeyError as e:
			raise ValueError("Invalid format string specifier: %s" % e)

def gitPassthrough(directory: Optional[str], ignore_errors: bool, args: List[str]) -> None:
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

	# Iterate over the root repo and its dependencies
	failed = 0
	for clone in iterDeps(str(rootRepo)):
		repo = git.Repo(str(clone.path))
		if clone.repospec.revision and repo.index.diff(None):
			print(f"{clone.repospec}: Unexpected changes in version-pinned repository")
		elif clone.repospec.revision and repo.commit(clone.repospec.revision) != repo.head.commit:
			print(f"{clone.repospec}: Wrong HEAD in version-pinned repository")
		else:
			print(clone.repospec)
		host = Host.load(name = clone.repospec.host)
		with repo.git.custom_environment(**makeGitEnvironment(host)):
			try:
				if clone.repospec.revision:
					if pinnedBehavior == 'skip':
						continue
					elif pinnedBehavior == 'reset':
						repo.remotes['origin'].fetch()
						repo.head.reset(clone.repospec.revision, hard = True)
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

def configCLI(key: Optional[str], value: Optional[str]) -> None:
	if key is None:
		for c in config.all():
			print(f"{c.key} = {c.value}")
	else:
		curValue = getattr(config, key)
		if value is None:
			print(curValue)
		else:
			print(f"Old value: {curValue}")
			newValue = str(Path(value).resolve())
			setattr(config, key, newValue)
			print(f"New value: {newValue}")

def mv(repospec: RepoSpec, dest: str) -> None:
	with repospec.lock():
		clone: Clone = where(repospec, 'py', 'skip')
		if clone is None:
			raise ValueError(f"No clone found for {repospec}") from None
		repospec, src = clone.repospec, clone.path
		dest = Path(dest).resolve()
		if dest.exists():
			if not dest.is_dir():
				raise ValueError(f"Destination already exists: {dest}")
			dest /= os.path.basename(dest)
			if dest.exists():
				raise ValueError(f"Destination already exists: {dest}")
		shutil.move(src, dest)
		clone.path = dest
		clone.save()
	print(f"{repospec} moved to {dest}")

def findRoot(dir: Optional[str]) -> Optional[Path]:
	# This could theoretically be one query that ORs together a bunch of paths
	path = (Path(dir) if dir is not None else Path.cwd()).resolve()
	clone = Clone.tryLoad(path = str(path))
	if clone:
		return clone.path
	for path in path.parents:
		clone = Clone.tryLoad(path = str(path))
		if clone:
			return clone.path

def prune(interactive: bool) -> None:
	removed, total = 0, 0
	for clone in Clone.loadAll():
		total += 1
		if not clone.path.exists():
			if interactive and input(f"Remove {clone.repospec} (missing clone {clone.path})? ").lower() not in ('y', 'yes'):
				continue
			clone.delete()
			removed += 1
			if not interactive:
				print(f"Removed {clone.repospec} (missing clone {clone.path})")
	print(f"Removed {removed}, kept {total - removed}")

def getCredential(host: str) -> str:
	return Host.load(name = host).password

def makeLock(key = 'test-lock') -> None:
	with db.lock(key):
		print(f"{os.getpid()}: Locked")
		with db.lock(key, 2):
			print(f"{os.getpid()}: Locked again. Sleeping...")
			time.sleep(5)

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
whatParser.add_argument('dir', nargs = '?', default = '.', help = 'directory to lookup')

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
addHostParser.add_argument('-p', '--password', nargs = '?', default = None, const = '-', help = "login password (empty or '-' to prompt)")
addHostParser.add_argument('-k', '--ssh-key', metavar = 'PEM', help = "ssh public key PEM filename")
addHostParser.add_argument('--clone-url', metavar = 'URL', help = "clone URL pattern")
addHostParser.add_argument('--force', action = 'store_true', help = 'add the host even if a connection cannot be established')

editHostParser = makeMode('edit-host', editHost, 'edit a registered git host')
editHostParser.add_argument('name', type = type_host_name)
editHostParser.add_argument('--new-url', metavar = 'URL')
editHostParser.add_argument('--new-username', metavar = 'USERNAME')
editHostParser.add_argument('--new-password', nargs = '?', const = '-', metavar = 'PASSWORD')
editHostParser.add_argument('--new-ssh-key', metavar = 'PEM')
editHostParser.add_argument('--new-clone-url', metavar = 'URL')
editHostParser.add_argument('--update-clones', action = 'store_true')
editHostParser.add_argument('--force', action = 'store_true')

rmHostParser = makeMode('rm-host', rmHost, 'remove a registered git host')
rmHostParser.add_argument('name', type = type_host_name)

depsParser = makeMode('deps', print_return(deps), "list information about a repo's dependencies")
depsParser.add_argument('repo', nargs = '?', type = type_repospec, default = None)
depsParser.add_argument('--format', default = '%p', help = 'Format to display each line in')

gitParser = makeMode('git', gitPassthrough, 'run a git command on the repo and all its dependencies')
gitParser.add_argument('-C', '--directory', metavar = 'DIR', default = '.', help = 'root directory')
gitParser.add_argument('-i', '--ignore-errors', action = 'store_true', help = "don't stop if the git command fails")
gitParser.add_argument('args', nargs = argparse.REMAINDER, help = 'arguments to pass to git')

configParser = makeMode('config', configCLI, 'get/set configuration key(s)')
configParser.add_argument('key', nargs = '?', help = 'configuration key to get/set; if omitted, all keys are shown')
configParser.add_argument('value', nargs = '?', help = 'value to set')

mvParser = makeMode('mv', mv, 'move a cloned repository on disk')
mvParser.add_argument('repospec', type = type_repospec)
mvParser.add_argument('dest')

findRootParser = makeMode('find-root', print_return(findRoot, '%(dir)s is not within a got repository'), 'find the root of a clone given a path within it')
findRootParser.add_argument('dir', nargs = '?', default = str(Path.cwd()), help = 'directory to start from')

pruneParser = makeMode('prune', prune, 'unregister clones that no longer exist on disk')
pruneParser.add_argument('-i', '--interactive', action = 'store_true', help = 'prompt before unregistering missing clones')

# This is used by git-credential, it's not meant for direct user interaction
getCredentialParser = makeMode('get-credential', print_return(getCredential, 'host has no stored password'), argparse.SUPPRESS)
getCredentialParser.add_argument('host')

# This is just for testing
# lockParser = makeMode('lock', makeLock, argparse.SUPPRESS)

# Running with no arguments (or with just -h/--help) will silently pick --where and then give you the help output for that mode, which is confusing. Print the general help instead
if len(sys.argv) == 1 or (len(sys.argv) == 2 and sys.argv[1] in ('-h', '--help')):
	parser.print_help()
	exit(0)

parser.set_defaults(modeParser = whereParser)

# First parse to isolate the mode; we get back a namespace containing 'modeParser' for the mode-specific parser, and a list of all the unprocessed arguments to pass on
args, extraArgs = parser.parse_known_args()

# Then use the mode-specific parser to do the real parse
modeArgs = args.modeParser.parse_args(extraArgs)

# And pass those args to the mode's handler (don't pass 'handler', it's not a real argument)
modeArgs.handler(**{k: v for k, v in vars(modeArgs).items() if k != 'handler'})
