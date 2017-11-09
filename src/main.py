import argparse
from getpass import getpass
import git
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time

from .DB import db, DB, Like
from .Credential import Credential
from .Config import config
from .Clone import Clone
from .Host import Host

from .RepoSpec import RepoSpec, HOST_PATTERN
from .utils import print_return, gotRoot, makeGitEnvironment, verbose, Template

# Type hints
from typing import *
URL = NewType('URL', str)
JSON = NewType('JSON', str)

# On Windows, use the system certificates instead of the bundled ones
if platform.system() == 'Windows' and 'REQUESTS_CA_BUNDLE' not in os.environ:
	import wincertstore
	ca = wincertstore.CertFile()
	ca.addstore('ROOT')
	ca.addstore('CA')
	os.environ['REQUESTS_CA_BUNDLE'] = ca.name

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
	# 'spec+' means the spec and its dependencies
	elif spec.endswith('+'):
		return [clone.repospec for clone in iterDeps(type_repospec(spec[:-1]))]
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

	def lookupRepo(pnt = None):
		if pnt is None:
			pnt = verbose(1)
		# Ambiguous repospecs are a problem. If 'repo' lacks a host, and we can find exactly one matching clone, we use it
		candidates = list(Clone.loadSpec(repo))
		if len(candidates) == 1:
			clone = candidates[0]
			# Make sure the local path actually exists
			if not ensure_on_disk or clone.path.is_dir():
				return formatRtn(clone)
			elif pnt:
				print(f"{repo}: local clone `{clone.path}' no longer exists")
			nonlocal dest
			if dest is None:
				dest = str(clone.path)
		elif len(candidates) > 1:
			raise RuntimeError(f"{repo}: Ambiguous repospec matches multiple clones: {', '.join(clone.repospec for clone in candidates)}")
		elif pnt:
			print(f"{repo}: no local clone on record")

	if 'GOT_WHERE_LOG' in os.environ:
		try:
			with open(os.environ['GOT_WHERE_LOG'], 'a') as f:
				f.write(f"{repo}\n")
		except IOError as e:
			print(f"Failed to log {repo} query to {os.environ['GOT_WHERE_LOG']}: {e}")

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
		rtn = lookupRepo(False)
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
		from .GitProgress import GitProgress

		# There seems to be a GitPython bug that prevent this from working well: https://github.com/gitpython-developers/GitPython/issues/444#issuecomment-320523860
		# In the meantime I just run git clone directly
		# git.Repo.clone_from(url, str(localPath), env = makeGitEnvironment(host), progress = GitProgress())

		progress = GitProgress() if verbose(1) and sys.stdout.isatty() else None

		env = dict(os.environ)
		env.update(makeGitEnvironment(host))
		proc = subprocess.Popen(['git', 'clone', '-v', '--progress', url, str(localPath)], env = env, stdout = subprocess.DEVNULL, stderr = subprocess.PIPE, universal_newlines = True)
		stderr = []
		if progress is not None:
			progress = GitProgress()
			handler = progress.new_message_handler()
			for line in proc.stderr:
				stderr.append(line)
				handler(line)
			progress.finish()
		else:
			for line in proc.stderr:
				stderr.append(line)
		if proc.wait() != 0:
			raise RuntimeError("Clone failed:\n" + ''.join(stderr))

		if repo.revision is not None:
			r = git.Repo(str(localPath))
			r.head.reference = r.commit(repo.revision)
			r.head.reset(index = True, working_tree = True)

		clone = Clone(repo, localPath)
		clone.save()
		return formatRtn(clone)

# This is an adapter for command-line where mode. 'repos' comes from an argument of type 'multipart_repospec' with '+' nargs, so it's a list of lists of repospecs that needs to be flattened and passed to where() individually
def whereCLI(repos: List[List[RepoSpec]], format: str, on_uncloned: str, dest: str, listen: bool, ignore_missing: bool):
	repos = [spec for l in repos for spec in l]
	if not repos and not listen:
		raise ValueError("One or more repospecs are required unless --listen is provided")
	if dest is not None and (len(repos) > 1 or listen):
		raise ValueError("Can't specify a clone destination with multiple repospecs or listen mode")

	lookup = lambda repo: where(repo, format, on_uncloned, not ignore_missing, dest)

	if format == 'json' and repos:
		# JSON format is a list instead of multiple lines
		yield json.dumps([json.loads(jsonObject) if jsonObject is not None else None for jsonObject in map(lookup, repos)])
	else:
		# In all other cases, print one line per repo
		for repo in repos:
			yield lookup(repo)

	if listen:
		for spec in sys.stdin:
			spec = spec.strip()
			if spec:
				# JSON format is a list instead of multiple lines
				if format == 'json':
					yield json.dumps([json.loads(jsonObject) if jsonObject is not None else None for jsonObject in map(lookup, type_multipart_repospec(spec))])
				else:
					for repo in type_multipart_repospec(spec):
						yield lookup(repo)

def here(repo: RepoSpec, dir: str, force: bool) -> Optional[Clone]:
	with repo.lock():
		existing: Clone = where(repo, 'py', 'skip', False)

		if dir == '-':
			if existing:
				existing.delete()
				print(f"{existing.repospec} no longer has a registered local clone")
				if existing.path.exists():
					print(f"(old path still exists on disk: {existing.path})")
			return

		if repo.host is None:
			# If the host is unspecified, look for one with a clone URL that matches the existing repo
			# IF the repo doesn't exist or no host has that clone URL, use the first one (this will only work in force mode)
			firstHost = None
			try:
				r = git.Repo(dir)
				actualUrl = r.remotes['origin'].url
				if verbose(2):
					print(f"No host specified -- searching for one with clone URL {actualUrl}")
			except:
				if not force:
					raise RuntimeError(f"Unable to deduce host without an existing clone. Specify the host in the repospec with <host>:{repo} or use --force to choose the first valid host")
				if verbose(2):
					print(f"No host specified and {dir} not a valid git host")
				actualUrl = None

			for host in Host.loadAll():
				if actualUrl is None:
					break
				if firstHost is None:
					firstHost = host
				try:
					cloneUrl = host.getCloneURL(repo.name)
					if cloneUrl == actualUrl:
						break
				except:
					pass
			else:
				if firstHost is None:
					raise RuntimeError("No hosts registered")
				host = firstHost
			repo.host = host.name
			if verbose(2):
				print(f"Deduced host {repo.host}")

		dir = Path(dir).resolve()
		if not force:
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
				print(f"  New clone URL: {new_clone_url}")

			try:
				host.check()
			except ConnectionError as e:
				if force:
					print(f"Host error (editing anyway): {e}")
				else:
					raise ConnectionError(f"Unable to edit host: {e}")

			if update_clones and ((new_url is not None) or (new_ssh_key is not None) or (new_clone_url is not None)):
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

def iterDeps(repo: Optional[RepoSpec], startPath: Optional[Path] = None) -> Iterable[Clone]:
	if startPath is None:
		if repo is None:
			try:
				repo = what(None)
			except RuntimeError:
				print("Current directory is not a tracked repository")
				return
		worklist = [repo]
	else:
		worklist = [RepoSpec.fromStr(depSpec) for depSpec in startPath.read_text().split()]

	seen = set()
	while worklist:
		repo = worklist.pop(0)
		if repo in seen:
			continue
		clone: Clone = where(repo, 'py', 'clone')
		seen.add(repo)
		yield clone

		depsPath = Path(clone.path) / 'deps.got'
		if depsPath.exists():
			worklist += [RepoSpec.fromStr(depSpec) for depSpec in depsPath.read_text().split() if depSpec not in seen]
		elif len(seen) == 1 and startPath is None: # This is the first repo, the one the user specified
			print(f"{repo} has no dependencies file ({depsPath})")

def deps(repo: Optional[RepoSpec], format: str, file: Optional[str]) -> Iterable[str]:
	if file is not None:
		file = Path(file)
		if not file.exists():
			raise ValueError(f"Dependency file does not exist: {file}")
	t = Template(format)
	for clone in iterDeps(repo, file):
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
	for clone in iterDeps(rootRepo):
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

def run(repos: Iterable[Iterable[RepoSpec]], cmd: List[str], bg: bool, ignore_errors: bool):
	env = dict(os.environ)
	procs = []
	for set in repos:
		for repo in set:
			clone: Clone = where(repo, 'py', 'clone')
			print(str(clone.repospec), file = sys.stderr)
			env['GOT_REPOSPEC'] = str(clone.repospec)

			if platform.system() == 'Windows':
				# Passing a whole command as a single string inside a list won't work on Windows, e.g. -x 'foo bar baz'. Turn it into a raw string instead
				shell = True
				if len(cmd) == 1:
					cmd = cmd[0]
			else:
				shell = (len(cmd) == 1)

			proc = subprocess.Popen(cmd, cwd = str(clone.path), shell = shell, env = env)
			procs.append(proc)
			if not bg:
				proc.wait()
				if not ignore_errors and proc.returncode != 0:
					raise RuntimeError(f"Failed on {repo}: exit code {proc.returncode}")
				print()
	# Wait for every process to exit. Then exit with the number of processes that failed
	exit(sum(proc.wait() != 0 for proc in procs))

def worktree(dir: Optional[str], temp: bool, with_repos: Optional[List[str]]):
	dir = Path(dir or tempfile.mkdtemp(prefix = 'got_worktree_')).resolve()
	if dir.exists():
		try:
			next(dir.iterdir())
			raise RuntimeError(f"{dir} already exists and contains files")
		except StopIteration:
			pass
	else:
		dir.mkdir(parents = True)

	env = dict(os.environ)
	env['GOT_WORKTREE'] = '1'
	env['GOT_PARENT_ROOT'] = str(gotRoot)
	env['GOT_ROOT'] = str(dir)
	if platform.system() == 'Windows':
		shell = [env.get('COMSPEC', 'cmd.exe')]
		promptVar, promptDefault = 'PROMPT', '$P$G'
	else:
		shell = [env.get('SHELL', '/bin/sh'), '-i']
		promptVar, promptDefault = 'PS1', r'\u@\h:\w\$'
	env[promptVar] = f"(worktree) {env.get(promptVar, promptDefault)}"
	print(f"Making {'temporary ' if temp else ''}worktree shell at {dir}")

	# Run got in the new root just to make the empty database
	# (The main reason to do this is so clone_root is set right. Might be easier to just patch that in the new database, but it feels ugly)
	if subprocess.Popen([sys.executable, sys.argv[0]], cwd = str(dir), env = env, stdout = subprocess.DEVNULL, stderr = subprocess.DEVNULL).wait() != 0:
		raise RuntimeError("Failed to run initial got in worktree dir")

	# Import info from the parent database
	worktreeDb = DB(dir)
	worktreeDb.worktreeSetup(db)
	# with_repos is None if none should be imported, [] if all should be imported, or [...] if just the listed patterns should be imported
	if with_repos is not None:
		worktreeDb.importRepos(db, with_repos or ['*'])

	# Run the shell
	res = subprocess.Popen(shell, cwd = str(dir), env = env).wait()

	# If the user specified a new retention setting within the worktree, use it
	for row in worktreeDb.select("SELECT value FROM config WHERE key = ?", 'worktree_keep'):
		temp = (row['value'] != 'true')
	worktreeDb.close()

	if temp:
		if res != 0:
			print(f"Temporary worktree exited {res}; preserving contents")
		else:
			print("Cleaning up worktree")
			shutil.rmtree(dir)

def worktreeActive(keep: Optional[bool], import_repos: Optional[List[str]]):
	didWork = False
	if keep is not None:
		config.worktree_keep = 'true' if keep else 'false'
		print(f"Worktree flagged for {'retention' if keep else 'deletion'} on exit")
		didWork = True
	if import_repos is not None:
		parentDb = DB(Path(os.environ['GOT_PARENT_ROOT']))
		db.importRepos(parentDb, import_repos)
		print(f"Worktree imported new repositories")
		didWork = True
	if not didWork:
		print(f"Currently in worktree: {gotRoot}")

def getCredential(host: str) -> str:
	return Host.load(name = host).password

def makeLock(key = 'test-lock') -> None:
	with db.lock(key):
		print(f"{os.getpid()}: Locked")
		with db.lock(key, 2):
			print(f"{os.getpid()}: Locked again. Sleeping...")
			time.sleep(5)

whereParser = makeMode('where', print_return(whereCLI), 'find the local path to a package, cloning it from a git host if necessary', ['local'])
whereParser.add_argument('repos', nargs = '*', type = type_multipart_repospec)
whereParser.add_argument('--format', choices = ['plain', 'json'], default = 'plain')
whereParser.add_argument('-o', '--output-file', dest = 'outputFile', metavar = 'FILE', default = sys.stdout, help = 'optional file to hold the results rather than stdout')
whereParser.add_argument('--outputFile', help = argparse.SUPPRESS) # backwards-compatible version of --output-file
group = whereParser.add_mutually_exclusive_group()
group.add_argument('--on-uncloned', choices = ['clone', 'skip', 'fail', 'fake'], default = 'clone', help = "what to do if the clone doesn't exist")
group.add_argument('--no-clone', action = 'store_const', dest = 'on_uncloned', const = 'skip', help = argparse.SUPPRESS) # backwards-compatibility version of --on-uncloned=skip
whereParser.add_argument('-d', '--dest', nargs = '?', default = None, help = 'where to store a new clone if one is made')
whereParser.add_argument('--ignore-missing', action = 'store_true', help = 'return a recorded path even if it no longer exists')
whereParser.add_argument('--listen', action = 'store_true', help = 'read repospecs interactively from stdin')

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
depsParser.add_argument('-f', '--file', default = None, help = 'File to read dependency information from')

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

runParser = makeMode('run', run, 'run an arbitrary command on the specified repositories')
runParser.add_argument('repos', nargs = '+', type = type_multipart_repospec)
runParser.add_argument('--bg', action = 'store_true', help = 'run command in the background on each repository in parallel')
runParser.add_argument('-i', '--ignore-errors', action = 'store_true', help = "don't stop if a command fails")
runParser.add_argument('-x', '--cmd', required = True, nargs = argparse.REMAINDER, help = 'command to run')

if 'GOT_WORKTREE' not in os.environ:
	worktreeParser = makeMode('worktree', worktree, 'make a new shell with an isolated got root')
	worktreeParser.add_argument('-d', '--dir', help = 'root path to use for new worktree')
	worktreeParser.add_argument('-t', '--temp', action = 'store_true', help = 'delete the worktree on exit')
	worktreeParser.add_argument('-r', '--with-repos', nargs = '*', help = 'import repos from the parent got')
else:
	worktreeParser = makeMode('worktree', worktreeActive, 'change properties of the current worktree')
	worktreeParser.add_argument('--keep', action = 'store_true', default = None, help = 'preserve the worktree on exit, even if created with --temp')
	worktreeParser.add_argument('--delete', action = 'store_false', dest = 'keep', help = 'delete the worktree on exit, even if created without --temp')
	worktreeParser.add_argument('-r', '--import-repos', nargs = '+', help = 'import (more) repos from the parent got')

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
