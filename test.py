import argparse
import colorama
import contextlib
import fnmatch
import git
import inspect
from json import loads as fromJS, dumps as toJS
import keyring
import os
from pathlib import Path
import platform
import re
import shutil
import string
import subprocess
import sys
import tempfile
import textwrap
import time
import timeit
import traceback
from typing import *
from unittest import main, TestCase, TestResult, TextTestRunner
from xml.etree import cElementTree as ET
import zipfile

class Template(string.Template):
	delimiter = '%'

parser = argparse.ArgumentParser()
parser.add_argument('--testrundir', '-d')
parser.add_argument('--host-data')
parser.add_argument('--junit', action = 'store_true')
parser.add_argument('--verbose', '-v', action = 'store_true')
parser.add_argument('--list', action = 'store_true', help = 'list test cases instead of running them')
parser.add_argument('tests', nargs = '*')
args, extraArgs = parser.parse_known_args()

gotDir = Path(__file__).resolve().parent
runDir = Path(args.testrundir or tempfile.mkdtemp(prefix = 'got-testrundir')).resolve()

class HideStr(str):
	def __repr__(self):
		return "'********'"

#TODO To minimize skipped tests if Bitbucket is unavailable, only depend on a Bitbucket host if necessary; make a fake daemon host otherwise
allHostData = fromJS(Path(args.host_data).read_text()) if args.host_data else {}
for v in allHostData.values():
	if 'password' in v:
		v['password'] = HideStr(v['password'])

class GotRun:
	def __init__(self, args, *, cwd = None, gotRootSubdir = None):
		self.args = args
		self.cwd = cwd
		self.gotRootSubdir = gotRootSubdir
		self._stdout, self._stderr = None, None
		self.checkedExit = False
		self.proc = None
		self.testname = inspect.stack()[1].function

	@property
	def stdout(self) -> str:
		if self._stdout is None:
			stdout, stderr = self.proc.communicate()
			self._stdout = stdout.decode('utf-8')
			self._stderr = stderr.decode('utf-8')

			# For some reason on Windows, stdout has an extra newline, and a couple of the tests are sensitive to it
			if platform.system() == 'Windows' and self._stdout.endswith('\r\n'):
				self._stdout = self._stdout[:-2]
		return self._stdout

	@property
	def stderr(self) -> str:
		if self._stderr is None:
			# Access the stdout property to load _stdout and _stderr
			self.stdout
		return self._stderr

	def makeCommand(self):
		args = [str(gotDir / 'got')] + [arg for arg in self.args if arg]
		if platform.system() == 'Windows':
			args[0] += '.bat'
		return args

	def makeEnvironment(self):
		env = os.environ.copy()
		# The got root is the test case directory under runDir, but we might be in a subdirectory
		root = Path.cwd()
		if root.parent != runDir:
			for root in root.parents:
				if root.parent == runDir:
					break
			else:
				raise RuntimeError(f"Current directory {Path.cwd().resolve()} is not within a test case rundir")
		if self.gotRootSubdir is not None:
			root /= self.gotRootSubdir
		env['GOT_ROOT'] = str(root)
		env['GOT_VERBOSE'] = '2'
		return env

	def __enter__(self):
		args = self.makeCommand()
		print(f"Run: {args}")
		print()
		self.proc = subprocess.Popen(args, cwd = self.cwd, env = self.makeEnvironment(), stdout = subprocess.PIPE, stderr = subprocess.PIPE)
		self.proc.__enter__()
		return self

	def __exit__(self, type, value, tb):
		if args.verbose:
			if self.stdout and not args.junit:
				print(colorama.Fore.CYAN + self.stdout + colorama.Fore.RESET)
			if self.stderr and not args.junit:
				print(colorama.Fore.RED + self.stderr + colorama.Fore.RESET)
		try:
			if not self.checkedExit:
				self.assertWorks()
		finally:
			self.proc.__exit__(type, value, tb)

	# Same behavior as unittest.fail
	def fail(self, msg):
		raise AssertionError(msg)

	def assertExitCode(self, code):
		self.checkedExit = True
		ret = self.proc.wait()
		if ret != code:
			self.fail(f"Got exited with code {ret}")

	def assertWorks(self):
		self.assertExitCode(0)

	def assertFails(self):
		self.checkedExit = True
		if self.proc.wait() == 0:
			self.fail("Got failed to detect a problem and exited 0")

	def assertInStdout(self, s):
		if s not in self.stdout:
			self.fail(f"Wrong stdout; expected to find: {s}")

	def assertStdoutMatches(self, pattern):
		if re.match(pattern, self.stdout) is None:
			self.fail("Wrong stdout; didn't match pattern")

	def assertInStderr(self, s):
		if s not in self.stderr:
			self.fail(f"Wrong stderr; expected to find: {s}")

	def assertStderrMatches(self, pattern):
		if re.match(pattern, self.stderr) is None:
			self.fail("Wrong stderr; didn't match pattern")

class Tests(TestCase):
	def addHost(self, type, name, url, username = None, password = None, sshKey = None, cloneUrl = None, cloneRoot = None, force = False, shouldWork = True, gotRootSubdir = None):
		args = ['--add-host', '-t', type, name, url]
		if username:
			args += ['-u', username]
		if password:
			args += ['-p', password]
		if sshKey:
			args += ['-k', sshKey]
		if cloneUrl:
			args += ['--clone-url', cloneUrl]
		if cloneRoot:
			args += ['--clone-root', cloneRoot]
		if force:
			args.append('--force')

		with GotRun(args, gotRootSubdir = gotRootSubdir) as r:
			if not shouldWork:
				r.assertFails()

	def addBitbucketHost(self, name = 'bitbucket', *, force = False):
		if not 'bitbucket' in allHostData:
			self.skipTest("No bitbucket host available")
		data = allHostData['bitbucket']
		if not (('username' in data and 'password' in data) or ('sshKey' in data and 'cloneUrl' in data)):
			self.skipTest("Bitbucket host doesn't have auth data")
		self.addHost('bitbucket', name, force = force, **{k: v for k, v in data.items() if k in ('url', 'username', 'password', 'sshKey', 'cloneUrl', 'cloneRoot')})
		return data

	def assertRepoOriginatesFrom(self, repoPath, originUrl):
		repo = git.Repo(str(repoPath))
		self.assertEqual([originUrl], list(repo.remotes.origin.urls))

	def test_help(self):
		with GotRun(['--help']) as r:
			r.assertWorks()
			r.assertInStdout('usage: ')

	def test_hosts_none_plain(self):
		with GotRun(['--hosts']) as r:
			r.assertStdoutMatches('No hosts configured')

	def test_hosts_plain(self):
		hostData = self.addBitbucketHost('bitbucket')
		for i in (1, 2, 3):
			self.addHost('daemon', f"fake{i}", f"http://fake{i}", f"fake{i}", "pw", force = True)
		self.addHost('bitbucket', 'fake-bitbucket', 'http://example.com', 'fake', 'pw', force = True)
		expectedStdout = f"""
          Name: bitbucket
          Type: bitbucket
           URL: {hostData['url']}
      Username: {hostData.get('username', '')}
  SSH key path: {hostData.get('sshKey', '')}
     Clone URL: {hostData.get('cloneUrl', '')}
    Clone root: {hostData['cloneRoot'] if 'cloneRoot' in hostData else '<global> ' + str(Path('repos').resolve() / 'bitbucket')}
  Total clones: 0

          Name: fake-bitbucket
          Type: bitbucket
           URL: http://example.com
      Username: fake
  SSH key path: None
     Clone URL: None
    Clone root: <global> {str(Path('repos').resolve() / 'fake-bitbucket')}
  Total clones: 0
        Status: Disconnected (Unable to connect to Bitbucket)

          Name: fake1
          Type: daemon
           URL: http://fake1
      Username: fake1
  SSH key path: None
     Clone URL: None
    Clone root: <global> {str(Path('repos').resolve() / 'fake1')}
  Total clones: 0

          Name: fake2
          Type: daemon
           URL: http://fake2
      Username: fake2
  SSH key path: None
     Clone URL: None
    Clone root: <global> {str(Path('repos').resolve() / 'fake2')}
  Total clones: 0

          Name: fake3
          Type: daemon
           URL: http://fake3
      Username: fake3
  SSH key path: None
     Clone URL: None
    Clone root: <global> {str(Path('repos').resolve() / 'fake3')}
  Total clones: 0
		"""
		with GotRun(['--hosts']) as r:
			self.assertEqual(r.stdout.strip().split(os.linesep), expectedStdout.strip().split('\n'))

	def test_hosts_none_json(self):
		with GotRun(['--hosts', '--format=json']) as r:
			json = fromJS(r.stdout)
			self.assertEqual(json, {})

	def test_hosts_json(self):
		hostData = self.addBitbucketHost('bitbucket')
		for i in (1, 2, 3):
			self.addHost('daemon', f"fake{i}", f"http://fake{i}", f"fake{i}", "pw", force = True)
		self.addHost('bitbucket', 'fake-bitbucket', 'http://example.com', 'fake', 'pw', force = True)
		with GotRun(['--hosts', '--format=json']) as r:
			json = fromJS(r.stdout)
			self.assertEqual(json, {
				'bitbucket': {
					'type': 'bitbucket',
					'url': hostData['url'],
					'username': hostData.get('username', ''),
					'ssh_key_path': hostData.get('sshKey', None),
					'clone_url': hostData.get('cloneUrl', None),
					'clone_root': hostData.get('cloneRoot', None),
					'effective_clone_root': str(Path('repos').resolve() / 'bitbucket'),
					# 'valid': True, #TODO Plan to add this field later
				},
				'fake1': {
					'type': 'daemon',
					'url': 'http://fake1',
					'username': 'fake1',
					'ssh_key_path': None,
					'clone_url': None,
					'clone_root': None,
					'effective_clone_root': str(Path('repos').resolve() / 'fake1'),
				},
				'fake2': {
					'type': 'daemon',
					'url': 'http://fake2',
					'username': 'fake2',
					'ssh_key_path': None,
					'clone_url': None,
					'clone_root': None,
					'effective_clone_root': str(Path('repos').resolve() / 'fake2'),
				},
				'fake3': {
					'type': 'daemon',
					'url': 'http://fake3',
					'username': 'fake3',
					'ssh_key_path': None,
					'clone_url': None,
					'clone_root': None,
					'effective_clone_root': str(Path('repos').resolve() / 'fake3'),
				},
				'fake-bitbucket': {
					'type': 'bitbucket',
					'url': 'http://example.com',
					'username': 'fake',
					'ssh_key_path': None,
					'clone_url': None,
					'clone_root': None,
					'effective_clone_root': str(Path('repos').resolve() / 'fake-bitbucket'),
					# 'valid': False,
				},
			})

	def test_host_duplicate_name(self):
		self.addHost('daemon', 'host', 'http://example.com', 'username', 'password', force = True)
		self.addHost('daemon', 'host', 'http://example.com', 'username2', 'password2', force = True, shouldWork = False)

	def test_host_bad_url(self):
		self.addHost('bitbucket', 'host', 'http://example.com', 'username', 'password', shouldWork = False)

	def test_host_bad_credentials(self):
		hostData = self.addBitbucketHost()
		if not ('username' in hostData and 'password' in hostData):
			self.skipTest("No username/password auth data")
		# Using a bad password for a valid username risks triggering a captcha check
		self.addHost('bitbucket', 'host', hostData['url'], hostData['username'] + '_neg_test', 'pw', shouldWork = False)

	def test_host_force(self):
		hostData = self.addBitbucketHost()
		if not ('username' in hostData and 'password' in hostData):
			self.skipTest("No username/password auth data")
		self.addHost('bitbucket', 'host1', 'http://example.com', 'username', 'password', force = True)
		self.addHost('bitbucket', 'host2', hostData['url'], hostData['username'] + '_neg_test', 'pw', force = True)

	def test_host_clone_root(self):
		hostData = self.addBitbucketHost('bitbucket')
		repospec1, repospec2 = hostData['repospecs'][:2]

		# Before changing host clone_root
		cloneRoot = Path('repos').resolve() / 'bitbucket'
		with GotRun([repospec1]) as r:
			clonePath = Path(r.stdout.strip()).resolve()
			self.assertTrue(cloneRoot in clonePath.parents, f"{cloneRoot} is not a parent of {clonePath}")

		# After changing host clone_root
		with GotRun(['--edit-host', 'bitbucket', '--set-clone-root', 'foobar']):
			pass
		cloneRoot = Path('foobar').resolve()
		with GotRun([repospec2]) as r:
			clonePath = Path(r.stdout.strip()).resolve()
			self.assertTrue(cloneRoot in clonePath.parents, f"{cloneRoot} is not a parent of {clonePath}")

	def test_edit_host_new_url(self):
		self.addBitbucketHost('bitbucket')
		with GotRun(['--edit-host', 'bitbucket', '--set-url', 'http://example.com', '--force']) as r:
			r.assertInStdout('New URL: http://example.com')

	def test_edit_host_new_username(self):
		hostData = self.addBitbucketHost('bitbucket')
		with GotRun(['--edit-host', 'bitbucket', '--set-username', hostData.get('username', 'username') + '_test', '--force']) as r:
			r.assertInStdout(f"New username: {hostData.get('username', 'username') + '_test'}")

	def test_edit_host_new_password(self):
		hostData = self.addBitbucketHost('bitbucket')
		if not ('username' in hostData and 'password' in hostData):
			self.skipTest("No username/password auth data")
		# Also changing username to avoid captcha problems
		# I'm assuming the current password is not 'pw'. Hopefully
		with GotRun(['--edit-host', 'bitbucket', '--set-username', hostData['username'] + '_test', '--set-password', 'pw', '--force']) as r:
			r.assertInStdout(f"New username: {hostData['username'] + '_test'}")
			r.assertInStdout(f"New password:")

	def test_edit_host_new_clone_root(self):
		hostData = self.addBitbucketHost('bitbucket')
		with GotRun(['--edit-host', 'bitbucket', '--set-clone-root', 'dir', '--force']) as r:
			r.assertInStdout(f"New clone root: dir")

	def test_rm_host(self):
		hostData = self.addBitbucketHost('bitbucket')
		with GotRun(['--rm-host', 'bitbucket']) as r:
			r.assertInStdout('Removed host bitbucket')

	def test_rm_host_with_clones(self):
		hostData = self.addBitbucketHost('bitbucket')
		numClones = min(2, len(hostData['repospecs']))
		with GotRun(hostData['repospecs'][:2]):
			pass
		with GotRun(['--rm-host', 'bitbucket']) as r:
			r.assertInStdout('Removed host bitbucket')
			r.assertInStdout(f"Unregistered {numClones} clone")

	def test_rm_host_invalid_name(self):
		with GotRun(['--rm-host', 'bad']) as r:
			r.assertFails()

	def test_clones(self):
		numClones = len(self.git_helper())
		with GotRun(['--clones']) as r:
			self.assertEqual(r.stdout.strip().split(os.linesep), [f"host:repo{i} -> {Path('repo%d' % i).resolve()}" for i in range(1, numClones + 1)])

	def whereHelper(self, numRepos, format, *, makeHost = True) -> Iterable[GotRun]:
		if makeHost:
			hostData = self.addBitbucketHost('bitbucket')
		else:
			hostData = allHostData['bitbucket']
		repospecs = hostData['repospecs'][:numRepos]
		with GotRun(repospecs + ['--format', format]) as r:
			r.assertWorks() # This makes sure got has run and finished before we start checking the clone results below
			if format == 'plain':
				reportedClonePaths = r.stdout.strip().split(os.linesep)
			elif format == 'json':
				reportedClonePaths = [e['path'] for e in fromJS(r.stdout)]
			else:
				raise ValueError(f"Unrecognized format: {format}")
			for (repospec, reportedClonePath) in zip(repospecs, reportedClonePaths):
				yield repospec, r
				clonePath = Path.cwd() / 'repos' / 'bitbucket' / repospec
				self.assertEqual(reportedClonePath, str(clonePath))
				self.assertTrue(clonePath.exists(), f"{repospec} clone not found at {clonePath}")
				self.assertRepoOriginatesFrom(clonePath, Template(hostData['cloneUrl']).substitute(rs = repospec))

	def test_where_one_plain(self):
		for repospec, r in self.whereHelper(1, 'plain'):
			r.assertInStderr(f"{repospec}: no local clone on record")
			clonePath = r.stdout.strip()
		for _, _ in self.whereHelper(1, 'plain', makeHost = False):
			clonePath2 = r.stdout.strip()
			self.assertEqual(clonePath, clonePath2)

	def test_where_one_json(self):
		for repospec, r in self.whereHelper(1, 'json'):
			json = fromJS(r.stdout)
			self.assertEqual(1, len(json))
			json = json[0]
			self.assertEqual({'repospec', 'path'}, set(json.keys()))
			self.assertEqual(f"bitbucket:{repospec}", json['repospec'])
			clonePath = json['path']
		for repospec, r in self.whereHelper(1, 'json', makeHost = False):
			json = fromJS(r.stdout)
			clonePath2 = json[0]['path']
			self.assertEqual(clonePath, clonePath2)

	def test_where_many_plain(self):
		clonePaths, clonePaths2 = [], []
		for repospec, r in self.whereHelper(2, 'plain'):
			r.assertInStderr(f"{repospec}: no local clone on record")
			clonePaths.append(r.stdout.strip())
		for _, _ in self.whereHelper(2, 'plain', makeHost = False):
			clonePaths2.append(r.stdout.strip())
		self.assertEqual(clonePaths, clonePaths2)

	def test_where_many_json(self):
		clonePaths = []
		for i, (repospec, r) in enumerate(self.whereHelper(2, 'json')):
			json = fromJS(r.stdout)[i]
			self.assertEqual({'repospec', 'path'}, set(json.keys()))
			self.assertEqual(f"bitbucket:{repospec}", json['repospec'])
			clonePaths.append(json['path'])
		for repospec, r in self.whereHelper(2, 'json', makeHost = False):
			json = fromJS(r.stdout)
			clonePaths2 = [e['path'] for e in json]
			self.assertEqual(clonePaths, clonePaths2)
			# Checked all the paths here, don't need to keep iterating
			break

	def test_where_many_with_dest(self):
		hostData = self.addBitbucketHost('bitbucket')
		with GotRun(hostData['repospecs'] + ['-d', 'dst']) as r:
			r.assertFails()
			self.assertFalse(Path('dst').exists(), "Destination was created")

	def test_where_existing_with_dest(self):
		for repospec, r in self.whereHelper(1, 'plain'):
			clonePath = r.stdout.strip()
		with GotRun([repospec, '-d', 'dst']) as r:
			clonePath2 = r.stdout.strip()
			self.assertEqual(clonePath, clonePath2)
			self.assertFalse(Path('dst').exists(), "Destination was created")

	def test_where_on_uncloned_skip(self):
		hostData = self.addBitbucketHost('bitbucket')
		repospec = hostData['repospecs'][0]
		with GotRun([repospec, '--on-uncloned=skip']) as r:
			r.assertStdoutMatches('^$')

	def test_where_on_uncloned_fail(self):
		hostData = self.addBitbucketHost('bitbucket')
		repospec = hostData['repospecs'][0]
		with GotRun([repospec, '--on-uncloned=fail']) as r:
			r.assertFails()

	def test_where_on_uncloned_fake(self):
		hostData = self.addBitbucketHost('bitbucket')
		repospec = hostData['repospecs'][0]
		with GotRun([repospec, '--on-uncloned=fake']) as r:
			r.assertInStdout('REPO_NOT_FOUND')

	def test_where_here_mode(self):
		hostData = self.addBitbucketHost('bitbucket')
		repospec = hostData['repospecs'][0]
		with GotRun([repospec]) as r: # Clone a repo
			clonePath = r.stdout.strip()
		with GotRun(['--here', repospec, '-']): # Unenroll it
			pass
		with GotRun([repospec]) as r: # Reenroll it
			clonePath2 = r.stdout.strip()
			r.assertInStderr("switching to here mode")
			self.assertEqual(clonePath, clonePath2)

	def test_where_fake_bitbucket_repo(self):
		hostData = self.addBitbucketHost('bitbucket')
		project = hostData['repospecs'][0].split('/')[0]
		with GotRun([f"{project}/fake_repo_that_hopefully_does_not_exist"]) as r:
			r.assertFails()
			if 'cloneUrl' not in hostData:
				r.assertInStderr(f"bitbucket: Repository {project}/fake_repo_that_hopefully_does_not_exist does not exist")

	def test_where_fake_bitbucket_project(self):
		hostData = self.addBitbucketHost('bitbucket')
		with GotRun([f"fake_project_that_hopefully_does_not_exist/foo"]) as r:
			r.assertFails()
			if 'cloneUrl' not in hostData:
				r.assertInStderr("bitbucket: Project fake_project_that_hopefully_does_not_exist does not exist")

	def test_where_invalid_bitbucket_repospec(self):
		hostData = self.addBitbucketHost('bitbucket')
		with GotRun(["no_slash"]) as r:
			r.assertFails()
			r.assertInStderr("bitbucket: Expected repository name of the form")

	def test_where_invalid_repospec(self):
		hostData = self.addBitbucketHost('bitbucket')
		with GotRun(["f[]#!"]) as r:
			r.assertFails()
			r.assertInStderr("Invalid repospec")

	def test_where_listen(self):
		self.deps_helper()

		# Going to run the command directly to control stdin/stdout, but using GotRun for the prep work
		r = GotRun(['--listen'])
		args = r.makeCommand()
		env = r.makeEnvironment()
		del r

		# This feels like a Python bug, but if I don't redirect stderr somewhere, subprocess closes the inherited pipe it got from us even though we're still using it.
		with subprocess.Popen(args, env = env, stdin = subprocess.PIPE, stdout = subprocess.PIPE, stderr = subprocess.DEVNULL) as proc:
			def test(spec, unfurlsTo = None):
				with self.subTest(spec = spec):
					if unfurlsTo is None:
						unfurlsTo = [spec]
					expected = [str(Path(e).resolve()) for e in unfurlsTo]

					proc.stdin.write(f"{spec}\n".encode('utf-8'))
					proc.stdin.flush()
					actual = [proc.stdout.readline().decode('utf-8').strip() for _ in unfurlsTo]
					self.assertEqual(expected, actual)

			test('repo1')
			test('repo2')
			test('repo1+', ['repo1', 'repo2', 'repo3', 'repo4'])
			test('repo2+', ['repo2', 'repo4'])

	def test_where_listen_json(self):
		self.deps_helper()

		# Going to run the command directly to control stdin/stdout, but using GotRun for the prep work
		r = GotRun(['--listen', '--format', 'json'])
		args = r.makeCommand()
		env = r.makeEnvironment()
		del r

		# This feels like a Python bug, but if I don't redirect stderr somewhere, subprocess closes the inherited pipe it got from us even though we're still using it.
		with subprocess.Popen(args, env = env, stdin = subprocess.PIPE, stdout = subprocess.PIPE, stderr = subprocess.DEVNULL) as proc:
			def test(spec, unfurlsTo = None):
				with self.subTest(spec = spec):
					if unfurlsTo is None:
						unfurlsTo = [spec]
					expected = [{'repospec': f"host:{e}", 'path': str(Path(e).resolve())} for e in unfurlsTo]

					proc.stdin.write(f"{spec}\n".encode('utf-8'))
					proc.stdin.flush()
					actual = fromJS(proc.stdout.readline().decode('utf-8').strip())
					self.assertEqual(expected, actual)

			test('repo1')
			test('repo2')
			test('repo1+', ['repo1', 'repo2', 'repo3', 'repo4'])
			test('repo2+', ['repo2', 'repo4'])

	def test_where_default_branch(self):
		# Make a "host" folder with a bunch of repos on different branches
		hostDir = Path('host')
		repos = [git.Repo.init(str(hostDir / f"repo{i}")) for i in range(5)]

		# repo0 just stays on master
		repos[0].index.commit('Initial commit')
		# repo1 also stays on master, but has a branch named foo
		repos[1].index.commit('Initial commit')
		repos[1].create_head('foo')
		# repo2 is on foo, depends on repo4
		repos[2].index.commit('Initial commit')
		repos[2].create_head('foo').checkout()
		(hostDir / 'repo2' / 'deps.got').write_text('repo4')
		repos[2].index.add(['deps.got'])
		repos[2].index.commit('Add deps.got')
		# repo3 is on bar, has no foo, depends on repo4
		repos[3].index.commit('Initial commit')
		repos[3].create_head('bar').checkout()
		(hostDir / 'repo3' / 'deps.got').write_text('repo4')
		repos[3].index.add(['deps.got'])
		repos[3].index.commit('Add deps.got')
		# repo4 is on master, has foo
		repos[4].index.commit('Initial commit')
		repos[4].create_head('foo')

		# HEADs of each repo: ['master', 'master', 'foo', 'bar', 'master']
		subtests = {
			None: ['master', 'master', 'foo', 'bar', 'master'],
			':head': ['master', 'master', 'foo', 'bar', 'master'],
			':inherit': ['master', 'master', 'foo', 'bar', 'master'],
			'foo': ['master', 'foo', 'foo', 'bar', 'foo'],

			# These are special-cased below
			'DEPS': ['master', 'master', 'foo', 'bar', 'foo'], # Run --deps from each directory after cloning it, which means repo2 will pull in 3 and 4
			'CWD': ['master', 'foo', 'foo', 'bar', 'foo'], # Clone 2, then clone the others from 2's directory, so they should all be on foo if they have it
		}

		for default_branch, expected_branches in subtests.items():
			subRoot = str(default_branch).replace(':', '_') # Can't use ':' in Windows paths
			# Make a host that pulls from the "host" folder
			self.addHost('daemon', 'host', os.path.realpath('host'), gotRootSubdir = subRoot)

			if default_branch is not None:
				with GotRun(['--config', 'default_branch', ':inherit' if default_branch in ('DEPS', 'CWD') else default_branch], gotRootSubdir = subRoot):
					pass

			if default_branch == 'CWD':
				with GotRun([f"repo2"], cwd = os.sep, gotRootSubdir = subRoot) as r:
					repoPath = r.stdout.strip()
					with GotRun([f"repo{i}" for i in range(5)], cwd = repoPath, gotRootSubdir = subRoot):
						pass

			for i, expected_branch in enumerate(expected_branches):
				with self.subTest(default_branch = default_branch, i = i):
					# Set the working directory to the system root in the hopes that it's not a git repo -- we want to avoid running from within a repo because in some default branch modes we'll inherit that repo's current branch
					with GotRun([f"repo{i}"], cwd = os.sep, gotRootSubdir = subRoot) as r:
						repoPath = r.stdout.strip()
						repo = git.Repo(repoPath)
					if default_branch == 'DEPS':
						print("Cloning dependencies")
						with GotRun(['--deps'], cwd = repoPath, gotRootSubdir = subRoot) as r:
							pass
					self.assertEqual(repo.active_branch.name, expected_branch)

	def test_here(self):
		hostData = self.addBitbucketHost('bitbucket')
		repospec = hostData['repospecs'][0]
		with GotRun([repospec]) as r: # Clone repo
			clonePath = r.stdout.strip()
		with GotRun(['--here', repospec, '-']) as r: # Unenroll
			r.assertInStdout("no longer has a registered local clone")
		with GotRun([repospec, '--on-uncloned=fail']) as r: # Make sure the repo is unenrolled
			r.assertFails()
		with GotRun(['--here', f"bitbucket:{repospec}", clonePath]): # Reenroll
			pass
		with GotRun([repospec, '--on-uncloned=fail']) as r: # Make sure the repo is reenrolled
			clonePath2 = r.stdout.strip()
			self.assertEqual(clonePath, clonePath2)

	def test_here_missing_path(self):
		hostData = self.addBitbucketHost('bitbucket')
		repospec = hostData['repospecs'][0]
		with GotRun(['--here', f"bitbucket:{repospec}", 'directory_that_does_not_exist']) as r:
			r.assertFails()
			r.assertInStderr('Path not found')

	def test_here_not_git_repo(self):
		hostData = self.addBitbucketHost('bitbucket')
		repospec = hostData['repospecs'][0]
		d = Path('new_dir')
		d.mkdir()
		print(d.resolve())
		with GotRun(['--here', f"bitbucket:{repospec}", str(d)]) as r:
			r.assertFails()
			r.assertInStderr('not a git repository')
		with GotRun(['--here', f"bitbucket:{repospec}", str(d), '--force']):
			pass

	def test_here_wrong_git_repo(self):
		hostData = self.addBitbucketHost('bitbucket')
		repospec, repospec2 = hostData['repospecs'][:2]
		with GotRun([repospec2]) as r:
			clonePath = r.stdout.strip()
		with GotRun(['--here', f"bitbucket:{repospec}", clonePath]) as r:
			r.assertFails()
			r.assertInStderr("does not have the correct origin URL")
		with GotRun(['--here', f"bitbucket:{repospec}", clonePath, '--force']):
			pass

	def test_here_no_hosts(self):
		git.Repo.init('repo').create_remote('origin', 'http://example.com/')
		with GotRun(['--here', 'foo/bar', 'repo']) as r:
			r.assertFails()
			r.assertInStderr('No hosts registered')

	def test_here_deduced_host(self):
		hostData = self.addBitbucketHost('bitbucket')
		repospec = hostData['repospecs'][0]
		git.Repo.init('repo').create_remote('origin', Template(hostData['cloneUrl']).substitute(rs = repospec))
		with GotRun(['--here', repospec, 'repo']) as r:
			r.assertInStdout('Deduced host bitbucket')

	def test_here_deduced_multiple_hosts(self):
		self.addHost('daemon', 'daemon', 'http://example.com', cloneUrl = 'http://example.com/%rs.git')
		hostData = self.addBitbucketHost('bitbucket')
		repospec = hostData['repospecs'][0]

		git.Repo.init('repo1').create_remote('origin', 'http://example.com/foo/bar.git')
		with GotRun(['--here', 'foo/bar', 'repo1']) as r:
			r.assertInStdout('Deduced host daemon')

		git.Repo.init('repo2').create_remote('origin', Template(hostData['cloneUrl']).substitute(rs = repospec))
		with GotRun(['--here', repospec, 'repo2']) as r:
			r.assertInStdout('Deduced host bitbucket')

	def test_here_deduced_host_force(self):
		self.addHost('daemon', 'daemon', 'http://localhost', 'user', 'pw', force = True)
		git.Repo.init('repo').create_remote('origin', 'http://example.com/')
		with GotRun(['--here', 'foo/bar', 'repo', '--force']) as r:
			r.assertInStdout('Deduced host daemon')

	def test_what_from_root(self):
		hostData = self.addBitbucketHost('bitbucket')
		repospec = f"bitbucket:{hostData['repospecs'][0]}"
		with GotRun([repospec]) as r:
			clonePath = r.stdout.strip()
		with GotRun(['--what', clonePath]) as r:
			reportedRepospec = r.stdout.strip()
			self.assertEqual(repospec, reportedRepospec)

	def test_what_from_subdir(self):
		hostData = self.addBitbucketHost('bitbucket')
		repospec = f"bitbucket:{hostData['repospecs'][0]}"
		with GotRun([repospec]) as r:
			clonePath = r.stdout.strip()
		subdir = Path(clonePath) / 'new_subdirectory_created_for_testing'
		subdir.mkdir()
		with GotRun(['--what', str(subdir)]) as r:
			reportedRepospec = r.stdout.strip()
			self.assertEqual(repospec, reportedRepospec)

	def test_what_from_non_repo(self):
		with GotRun(['--what']) as r:
			r.assertFails()
			r.assertInStderr("Not a got repository")

	def test_whence_plain(self):
		hostData = self.addBitbucketHost('bitbucket')
		repospec = hostData['repospecs'][0]
		with GotRun(['--whence', repospec]) as r:
			cloneUrl = r.stdout.strip()
			self.assertEqual(cloneUrl, Template(hostData['cloneUrl']).substitute(rs = repospec))

	def test_whence_json(self):
		hostData = self.addBitbucketHost('bitbucket')
		repospec = hostData['repospecs'][0]
		with GotRun(['--whence', repospec, '--format=json']) as r:
			json = fromJS(r.stdout.strip())
			self.assertEqual(json, {
				'repospec': repospec,
				'host': 'bitbucket',
				'url': Template(hostData['cloneUrl']).substitute(rs = repospec),
			})

	def test_whence_bad(self):
		hostData = self.addBitbucketHost('bitbucket')
		if 'cloneUrl' in hostData:
			self.skipTest("Using hardcoded clone URL")
		with GotRun(['--whence', 'bad_project/bad_repospec']) as r:
			r.assertFails()
		with GotRun(['--whence', 'bad_project/bad_repospec', '--format=json']) as r:
			r.assertFails()

	def deps_helper(self):
		# Make some repos that depend on each other, and force-enroll them in a fake host
		deps = {
			'repo1': ['repo2', 'repo3'],
			'repo2': ['repo4'],
			'repo3': [],
			'repo4': [],
		}

		self.addHost('daemon', 'host', 'http://localhost', 'user', 'pw', force = True)
		for name, repoDeps in deps.items():
			d = Path(name)
			d.mkdir()
			if repoDeps:
				(d / 'deps.got').write_text('\n'.join(repoDeps))
			with GotRun(['--here', f"host:{name}", str(d), '--force']):
				pass

	def test_deps_cwd(self):
		self.deps_helper()
		testRoot = Path.cwd()
		expected = {
			'repo1': {'repo1', 'repo2', 'repo3', 'repo4'},
			'repo2': {'repo2', 'repo4'},
			'repo3': {'repo3'},
			'repo4': {'repo4'},
		}
		for name, expectedDeps in expected.items():
			with self.subTest(name = name):
				with chdir(name):
					with GotRun(['--deps']) as r:
						self.assertEqual(set(r.stdout.strip().split(os.linesep)), {str((testRoot / n).resolve()) for n in expectedDeps})
						if len(expectedDeps) == 1:
							r.assertInStderr(f"host:{name} has no dependencies file")

	def test_deps_repospec(self):
		self.deps_helper()
		expectedDeps = {'repo2', 'repo4'}
		with GotRun(['--deps', 'repo2']) as r:
			self.assertEqual(set(r.stdout.strip().split(os.linesep)), {str(Path(n).resolve()) for n in expectedDeps})

	def test_deps_bad_repospec(self):
		with GotRun(['--deps', 'bad_repospec']) as r:
			r.assertFails()

	def test_deps_format(self):
		# This uses git_helper because it needs actual git repos, not just folders
		r1, r2, r3 = self.git_helper()

		with GotRun(['--deps', 'repo1', '--format', 'foo %rs bar:%RS -- %p']) as r:
			expected = {
				f"foo repo1 bar:host:repo1 -- {Path('repo1').resolve()}",
				f"foo repo2 bar:host:repo2 -- {Path('repo2').resolve()}",
				f"foo repo3 bar:host:repo3 -- {Path('repo3').resolve()}",
			}
			self.assertEqual(set(r.stdout.strip().split(os.linesep)), expected)

		with GotRun(['--deps', 'repo1', '--format', '%rs %h... %H']) as r:
			expected = {
				f"repo1 {r1.head.commit.hexsha[:7]}... {r1.head.commit.hexsha}",
				f"repo2 {r2.head.commit.hexsha[:7]}... {r2.head.commit.hexsha}",
				f"repo3 {r3.head.commit.hexsha[:7]}... {r3.head.commit.hexsha}",
			}
			self.assertEqual(set(r.stdout.strip().split(os.linesep)), expected)

	def test_deps_bad_format(self):
		self.deps_helper()
		with GotRun(['--deps', 'repo1', '--format', '%bad']) as r:
			r.assertFails()
			r.assertInStderr("Invalid format string specifier")

	def test_deps_circular_dependency(self):
		self.deps_helper()
		Path('repo3/deps.got').write_text('repo1\nrepo2\nrepo3\nrepo4')
		with GotRun(['--deps', 'repo1', '--format', '%rs']) as r:
			expected = {'repo1', 'repo2', 'repo3', 'repo4'}
			self.assertEqual(set(r.stdout.strip().split(os.linesep)), expected)

	def git_helper(self):
		self.addHost('daemon', 'host', 'http://localhost', 'user', 'pw', force = True)

		r1 = git.Repo.init('repo1')
		Path('repo1/deps.got').write_text("repo2\nrepo3\n")
		r1.index.add(['deps.got'])
		r1.index.commit('Commit')
		with GotRun(['--here', 'host:repo1', 'repo1', '--force']):
			pass

		r2 = git.Repo.init('repo2')
		r2.index.commit('Commit')
		with GotRun(['--here', 'host:repo2', 'repo2', '--force']):
			pass

		r3 = git.Repo.init('repo3')
		r3.index.commit('Commit')
		with GotRun(['--here', 'host:repo3', 'repo3', '--force']):
			pass

		return r1, r2, r3

	def test_git_cwd(self):
		r1, r2, r3 = self.git_helper()
		with chdir('repo1'):
			with GotRun(['--git', 'show-ref']) as r:
				expected = {
					'host:repo1': f"{r1.head.commit.hexsha} refs/heads/master",
					'host:repo2': f"{r2.head.commit.hexsha} refs/heads/master",
					'host:repo3': f"{r3.head.commit.hexsha} refs/heads/master",
				}
				lines = [line for line in r.stdout.split(os.linesep) if line]
				actual = dict(zip(lines[::2], lines[1::2]))
				self.assertEqual(expected, actual)

	def test_git_dir(self):
		r1, r2, r3 = self.git_helper()
		with GotRun(['--git', '-C', 'repo1', 'show-ref']) as r:
			expected = {
				'host:repo1': f"{r1.head.commit.hexsha} refs/heads/master",
				'host:repo2': f"{r2.head.commit.hexsha} refs/heads/master",
				'host:repo3': f"{r3.head.commit.hexsha} refs/heads/master",
			}
			lines = [line for line in r.stdout.split(os.linesep) if line]
			actual = dict(zip(lines[::2], lines[1::2]))
			self.assertEqual(expected, actual)

	def test_git_ignore_errors(self):
		_, r2, _ = self.git_helper()
		with GotRun(['--git', '-C', 'repo1', '--ignore-errors', 'show', r2.head.commit.hexsha]) as r:
			r.assertInStdout('Ignored error')

	all_config_keys = ['clone_retries', 'clone_root', 'default_branch']

	def test_config_list_all(self):
		with GotRun(['--config']) as r:
			lines = r.stdout.strip().split(os.linesep)
			vals = dict(line.split(' = ') for line in lines)
			self.assertEqual(list(vals.keys()), self.all_config_keys)

	def test_config_get(self):
		with GotRun(['--config', 'clone_root']) as r:
			val = r.stdout.strip()
			self.assertEqual(val, str(Path('repos').resolve()))

	def test_config_set(self):
		with GotRun(['--config', 'clone_root', 'foobar']) as r:
			r.assertInStdout(f"Old value: {Path('repos').resolve()}")
			r.assertInStdout(f"New value: {Path('foobar').resolve()}")

	def test_config_clone_root(self):
		hostData = self.addBitbucketHost('bitbucket')
		repospec1, repospec2 = hostData['repospecs'][:2]

		# Before changing clone_root
		cloneRoot = Path('repos').resolve()
		with GotRun([repospec1]) as r:
			clonePath = Path(r.stdout.strip()).resolve()
			self.assertTrue(cloneRoot in clonePath.parents, f"{cloneRoot} is not a parent of {clonePath}")

		# After changing clone_root
		with GotRun(['--config', 'clone_root', 'foobar']):
			pass
		cloneRoot = Path('foobar').resolve()
		with GotRun([repospec2]) as r:
			clonePath = Path(r.stdout.strip()).resolve()
			self.assertTrue(cloneRoot in clonePath.parents, f"{cloneRoot} is not a parent of {clonePath}")

	def test_mv(self):
		self.deps_helper()
		with GotRun(['repo1']) as r:
			clonePath = Path(r.stdout.strip())
			self.assertTrue(clonePath.exists())
		with GotRun(['--mv', 'repo1', 'new_dir']) as r:
			r.assertInStdout('host:repo1 moved')
		self.assertFalse(clonePath.exists())
		with GotRun(['repo1']) as r:
			clonePath = Path(r.stdout.strip())
			self.assertTrue(clonePath.exists())
			self.assertEqual(os.path.basename(clonePath), 'new_dir')

	def test_mv_bad_repospec(self):
		with GotRun(['bad_repospec', 'dst']) as r:
			r.assertFails()

	def test_mv_dest_exists(self):
		self.deps_helper()
		Path('new_path').touch()
		with GotRun(['--mv', 'repo1', 'new_path']) as r:
			r.assertFails()
			r.assertInStderr('Destination already exists')

	def test_find_root_cwd(self):
		self.deps_helper()
		with chdir('repo1'):
			with GotRun(['--find-root']) as r:
				expected = Path.cwd().resolve()
				actual = Path(r.stdout.strip())
				self.assertEqual(expected, actual)

	def test_find_root_dir(self):
		self.deps_helper()
		with GotRun(['--find-root', 'repo1']) as r:
			expected = Path('repo1').resolve()
			actual = Path(r.stdout.strip())
			self.assertEqual(expected, actual)

	def test_find_root_subdir(self):
		self.deps_helper()
		Path('repo1/subdir').mkdir()
		with GotRun(['--find-root', 'repo1/subdir']) as r:
			expected = Path('repo1').resolve()
			actual = Path(r.stdout.strip())
			self.assertEqual(expected, actual)

	def test_find_root_bad_dir(self):
		with GotRun(['--find-root']) as r:
			r.assertFails()
			r.assertInStderr(f"{Path.cwd().resolve()} is not within a got repository")

	def test_prune(self):
		self.deps_helper()
		with GotRun(['--prune']) as r:
			r.assertInStdout('Removed 0, kept 4')
		shutil.rmtree('repo2')
		shutil.rmtree('repo3')
		shutil.rmtree('repo4')
		with GotRun(['--prune']) as r:
			r.assertInStdout('Removed 3, kept 1')
			r.assertInStdout('Removed host:repo2')
			r.assertInStdout('Removed host:repo3')
			r.assertInStdout('Removed host:repo4')
		with GotRun(['--prune']) as r:
			r.assertInStdout('Removed 0, kept 1')

	def test_scan(self):
		hostData = self.addBitbucketHost('bitbucket')
		repospecs = hostData['repospecs'][:5]
		# Clone all the repos
		with GotRun(repospecs):
			pass
		# Unregister two
		for repospec in repospecs[:2]:
			with GotRun(['--here', repospec, '-']):
				pass
		# Scan
		with GotRun(['--scan', '.']) as r:
			r.assertInStdout(f"Processing {len(repospecs)} repositories")
			for repospec in repospecs[:2]:
				r.assertInStdout(f"{os.path.join('repos', 'bitbucket', *repospec.split('/'))}: registered as bitbucket:{repospec}")
			r.assertInStdout("Scan complete. Added 2 clones")

	#TODO Test {--prune,--scan} --interactive? No ability to control stdin yet

	def test_db_v0_to_v1(self):
		Path('hosts.json').write_text(toJS({
			'host1': {
				'type': 'bitbucket',
				'url': 'http://bad-host/one',
			},
			'host2': {
				'type': 'daemon',
				'url': 'http://bad-host/two',
			},
		}))
		useKeyring = not isinstance(keyring.get_keyring(), keyring.backends.fail.Keyring)
		Path('credentials.json').write_text(toJS({
			'host1': {
				'username': 'user1',
				'password': '' if useKeyring else 'pass1',
			},
			'host2': {
				'username': 'user2',
				'password': '' if useKeyring else 'pass2',
			},
		}))
		if useKeyring:
			keyring.set_password('host1', 'user1', 'pass1')
			keyring.set_password('host2', 'user2', 'pass2')
		repo1, repo2 = Path('repo1').resolve(), Path('repo2').resolve()
		repo1.mkdir()
		repo2.mkdir()
		Path('clones.json').write_text(toJS({
			'host1:project/repo': str(repo1),
			'host2:project/repo2@000000': str(repo2),
		}))

		with GotRun(['--hosts', '--format', 'json']) as r:
			r.assertInStderr('Imported old JSON database')
			data = fromJS(r.stdout)
			self.assertEqual(set(data), {'host1', 'host2'})
			self.assertEqual(data['host1']['type'], 'bitbucket')
			self.assertEqual(data['host1']['url'], 'http://bad-host/one')
			self.assertEqual(data['host1']['username'], 'user1')
			self.assertEqual(data['host2']['type'], 'daemon')
			self.assertEqual(data['host2']['url'], 'http://bad-host/two')
			self.assertEqual(data['host2']['username'], 'user2')

		self.assertFalse(Path('hosts.json').exists())
		self.assertFalse(Path('credentials.json').exists())
		self.assertFalse(Path('clones.json').exists())
		self.assertTrue(Path('old_database.zip').exists())
		with zipfile.ZipFile('old_database.zip') as zip:
			self.assertEqual({'hosts.json', 'credentials.json', 'clones.json'}, set(zip.namelist()))

		with GotRun(['project/repo']) as r:
			r.assertStdoutMatches(re.escape(str(repo1)))
		with GotRun(['project/repo2@000000']) as r:
			r.assertStdoutMatches(re.escape(str(repo2)))
		with GotRun(['project/repo2']) as r:
			r.assertFails()

	def test_non_existent_got_root(self): #38
		gotRoot = Path('new_directory')
		self.assertFalse(gotRoot.exists())
		args = [str(gotDir / 'got')]
		if platform.system() == 'Windows':
			args[0] += '.bat'
		env = os.environ.copy()
		env['GOT_ROOT'] = str(gotRoot)
		with subprocess.Popen(args, env = env) as p:
			self.assertEqual(0, p.wait())
		self.assertTrue(gotRoot.exists())

	def test_repospec_with_deps(self):
		self.deps_helper()
		with GotRun(['repo2+']) as r:
			expectedDeps = {Path('repo2'), Path('repo4')}
			self.assertEqual(set(r.stdout.strip().split(os.linesep)), set(str(p.resolve()) for p in expectedDeps))
		with GotRun(['repo2+', 'repo1']) as r:
			expectedDeps = {Path('repo1'), Path('repo2'), Path('repo4')}
			self.assertEqual(set(r.stdout.strip().split(os.linesep)), set(str(p.resolve()) for p in expectedDeps))

	def test_run(self):
		self.deps_helper()
		cmd = 'cd' if platform.system() == 'Windows' else 'pwd'
		specs = ['repo1', 'repo2', 'repo3']
		with GotRun(['--run'] + specs + ['-x', cmd]) as r:
			for spec in specs:
				r.assertInStderr(spec)
				r.assertInStdout(str(Path(spec).resolve()))

	def test_run_bg(self):
		self.deps_helper()
		# Want a command to run for 10 seconds. This is surprisingly hard on Windows, 'timeout' is really poorly implemented
		cmd = 'ping 127.0.0.1 -n 10' if platform.system() == 'Windows' else 'sleep 10'
		specs = ['repo1', 'repo2', 'repo3', 'repo4']
		with GotRun(['--run'] + specs + ['--bg', '-x', cmd]) as r:
			# There are 4 invocations each sleeping for 10 seconds, but simultaneously, so this should take 10 seconds.
			# Leave a lot of padding and make sure it's less than 25 seconds
			start = timeit.default_timer()
			r.assertWorks()
			end = timeit.default_timer()
			self.assertTrue(end - start < 25)

	def test_run_ignore_errors(self):
		self.deps_helper()
		with GotRun(['--run', 'repo1', 'repo2', 'repo3', '--ignore-errors', '-x', 'command-that-does-not-exist']) as r:
			r.assertExitCode(3)

	#TODO Test --worktree?

@contextlib.contextmanager
def chdir(path):
	old = Path.cwd()
	os.chdir(path)
	try:
		yield
	finally:
		os.chdir(old)

if args.tests:
	for name in args.tests:
		if name.startswith('Tests.test_'):
			pass
		elif name.startswith('test_'):
			name = 'Tests.' + name
		else:
			name = 'Tests.test_' + name
		if '*' in name:
			ptn = name[6:]
			extraArgs += ['Tests.' + n for n in dir(Tests) if fnmatch.fnmatchcase(n, ptn)]
		else:
			extraArgs.append(name)

# Wrap each test method so it prints its name and switches to a dedicated result directory
for n in dir(Tests):
	if n.startswith('test_'):
		if args.list:
			print(n[5:])
			continue
		def closure(name, testFn):
			def f(*fnArgs, **kw):
				print()
				print()
				if not args.junit:
					print(colorama.Fore.BLACK + colorama.Back.WHITE + colorama.Style.BRIGHT + ("%-80s" % name) + colorama.Style.RESET_ALL)
				path = runDir / name
				try:
					shutil.rmtree(path)
				except FileNotFoundError:
					pass
				os.makedirs(path)
				with chdir(path):
					return testFn(*fnArgs, **kw)
			return f
		setattr(Tests, n, closure(n[5:], getattr(Tests, n)))

if args.list:
	exit(0)

# This unfortunately relies on a decent amount of introspection into TestResult, so it might break with new versions of unittest
class GotTestResult(TestResult):
	def __init__(self, *args, **kw):
		super().__init__(*args, **kw)
		self.junitRoot = ET.Element('testsuite')

	def startTest(self, test):
		super().startTest(test)
		self._mirrorOutput = True
		suite, case = test.id().rsplit('.', 1)
		# suite is '__main__.Tests', which is kind of useless; change to 'test.<platform name>' instead
		suite = 'test.' + platform.system().lower()
		self.junitCase = ET.SubElement(self.junitRoot, 'testcase', classname = suite, name = case)

	def stopTest(self, test):
		if self._stdout_buffer is not None:
			ET.SubElement(self.junitCase, 'system-out').text = self._stdout_buffer.getvalue()
		if self._stderr_buffer is not None:
			ET.SubElement(self.junitCase, 'system-err').text = self._stderr_buffer.getvalue()
		super().stopTest(test)

	def formatErr(self, err):
		return {'type': str(err[0]), 'message': textwrap.dedent(''.join(traceback.format_tb(err[2])))}

	def addError(self, test, err):
		ET.SubElement(self.junitCase, 'error', **self.formatErr(err))

	def addFailure(self, test, err):
		ET.SubElement(self.junitCase, 'failure', **self.formatErr(err))

	def addSkip(self, test, reason):
		# Looks like Jenkins doesn't support skip messages
		ET.SubElement(self.junitCase, 'skipped')

	def stopTestRun(self):
		ET.ElementTree(self.junitRoot).write(str(runDir / 'junit.xml'))

if not args.junit:
	colorama.init()
try:
	main(argv = sys.argv[:1] + extraArgs, testRunner = TextTestRunner(resultclass = GotTestResult, buffer = True) if args.junit else TextTestRunner)
finally:
	sys.stdout = sys.__stdout__
	sys.stderr = sys.__stderr__
