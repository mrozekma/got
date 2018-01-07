import abc
from contextlib import contextmanager
import git
import os
from pathlib import Path
import re
import stashy

from .Credential import Credential
from .Config import config
from .DB import db, ActiveRecord
from .utils import makeGitEnvironment, Template

class Host(abc.ABC):
	subclasses = {}

	def __new__(cls, name, type, *args, **kw):
		if type not in Host.subclasses:
			raise ValueError(f"Unknown type `{type}'")
		return Host.subclasses[type](name, *args, **kw)

	# Proxy ActiveRecord methods:

	@staticmethod
	def count():
		return sum(cls.count() for cls in Host.subclasses.values())

	@staticmethod
	def load(*, type = None, **attrs):
		if type is not None:
			return Host.subclasses[type].load(**attrs)
		for cls in Host.subclasses.values():
			try:
				# Need to call ActiveRecord.load (a classmethod) while specifying the class.
				# ActiveRecord.load.__func__ is the non-classmethod form of the method
				return ActiveRecord.load.__func__(cls, **attrs)
			except ValueError:
				pass
		raise ValueError("Host database lookup failed")

	@staticmethod
	def tryLoad(*, type = None, **attrs):
		try:
			return Host.load(type = type, **attrs)
		except ValueError:
			return None

	@staticmethod
	def loadAll(*, type = None, **attrs):
		return [host for cls in Host.subclasses.values() for host in cls.loadAll(**attrs)] if type is None else Host.subclasses[type].loadAll(**attrs)

# Concrete hosts don't subclass Host because __new__ interferes with their construction
class SubclassableHost:
	def __init__(self, name, url, username, ssh_key_path = None, clone_url = None, clone_root = None):
		self.type = self.getType()
		self.name = name
		self.url = url.rstrip('/')
		self.username = username
		self.ssh_key_path = ssh_key_path
		self.clone_url = clone_url
		self.clone_root = clone_root

	# This doesn't implement setting the password because it would need to wait until the host's save() method is called. Changing the password should be done via the Credential interface directly
	@property
	def password(self):
		cred = self.getCredential()
		return cred.password if cred is not None else None

	@property
	def clone_root(self):
		return self._clone_root

	@clone_root.setter
	def clone_root(self, clone_root):
		if clone_root is None:
			self._clone_root = None
		else:
			p = Path(clone_root)
			p.mkdir(parents = True, exist_ok = True)
			self._clone_root = str(p.resolve())

	def getCredential(self):
		try:
			return Credential.load(self.name, self.username)
		except ValueError:
			return None

	# The necessity of locking hosts is debatable, but the framework is there so I did it
	@contextmanager
	def lock(self):
		with db.lock(f"host.{self.name}"):
			yield

	@classmethod
	def __init_subclass__(cls):
		super().__init_subclass__()
		Host.subclasses[cls.getType()] = cls

	def getCloneURLFromPattern(self, repoName):
		return Template(self.clone_url).substitute(
			username = self.username,
			rs = repoName,
		)

	def makeCloneRE(self):
		if self.clone_url is None:
			raise RuntimeError(f"Host {self.name} has no clone URL pattern; cannot make a reverse clone regex")
		# First, escape the pattern so it can be used in a regex
		pattern = re.escape(self.clone_url)
		# But don't escape the template substitution character
		pattern = pattern.replace(rf'\{Template.delimiter}', Template.delimiter)
		# Now replace '%username' with the host username, and '%rs' with a named capture group
		pattern = Template(pattern).substitute(username = self.username, rs = '(?P<rs>.*)')
		# And compile it
		return re.compile(pattern)

	def getEffectiveCloneRoot(self):
		return Path(self.clone_root) if self.clone_root is not None else (Path(config.clone_root) / self.name)

	@abc.abstractmethod
	def getType(self = None):
		pass

	@abc.abstractmethod
	def getCloneURL(self, repoName):
		pass

	def check(self):
		pass

class BitbucketHost(SubclassableHost, ActiveRecord):
	def __init__(self, name, url, username, ssh_key_path = None, clone_url = None, clone_root = None):
		self._conn = None # Lazy loaded via self.conn property
		super().__init__(name, url, username, ssh_key_path, clone_url, clone_root)

	@property
	def conn(self):
		if self._conn is None:
			self._conn = stashy.connect(self.url, self.username, self.password)
		return self._conn

	def __setattr__(self, k, v):
		super().__setattr__(k, v)
		# Invalidate connection if any of the connection parameters have changed
		# This can't see password changes, so the main script is careful to set 'username' when the password changes
		if k in ('url', 'username'):
			self._conn = None

	def check(self):
		# Test connection
		if self.password is None:
			if self.ssh_key_path is None:
				raise ConnectionError("Either a password or an SSH key is required for Bitbucket access")
			return
		try:
			self.conn.projects.list()
		except stashy.errors.NotFoundException:
			raise ConnectionError("Unable to connect to Bitbucket")
		except stashy.errors.AuthenticationException as e:
			raise ConnectionError(str(e))

	def getType(self = None):
		return 'bitbucket'

	def getCloneURL(self, name):
		try:
			project, repoName = name.split('/')
		except ValueError:
			raise ValueError("Expected repository name of the form <project>/<repository>")

		if self.clone_url is not None:
			return self.getCloneURLFromPattern(name)
		if self.password is None:
			raise RuntimeError(f"Unable to access Bitbucket API to determine clone URL -- host `{self.name}' must be configured with a manual clone URL or a username/password")

		try:
			data = self.conn.projects[project].repos[repoName].get()
		except stashy.errors.NotFoundException as e:
			raise ConnectionError(str(e))
		except stashy.errors.AuthenticationException as e:
			raise ConnectionError(str(e))

		if data['scmId'] != 'git':
			raise RuntimeError("{repoPath} is not a git repository ({data['scmId']})")
		urls = {clone['name']: clone['href'] for clone in data['links']['clone']}
		if self.ssh_key_path is not None and 'ssh' in urls:
			return urls['ssh']
		elif self.password is not None and 'http' in urls:
			return urls['http']
		else:
			raise ValueError("No compatible clone links found")

	def getReposInProject(self, project):
		if self.password is None:
			raise RuntimeError(f"Unable to access Bitbucket API to query project repository list -- host `{self.name}' must be configured with a username/password")
		try:
			return [json['name'] for json in self.conn.projects[project].repos.all()]
		except stashy.errors.NotFoundException as e:
			raise ConnectionError(str(e))
		except stashy.errors.AuthenticationException:
			raise ConnectionError("Invalid/insufficient credentials")

class DaemonHost(SubclassableHost, ActiveRecord):
	def __init__(self, name, url, username, ssh_key_path = None, clone_url = None, clone_root = None):
		super().__init__(name, url, username, ssh_key_path, clone_url, clone_root)

	def getType(self = None):
		return 'daemon'

	def getCloneURL(self, name):
		if self.clone_url is not None:
			return self.getCloneURLFromPattern(name)

		# Nothing stops 'name' from escaping the path specified by self.url, like '../../../foo'. I can't see a problem with allowing it other than that it's weird, and allowing normal subdirectory traversal could be useful, so not currently putting any restrictions on 'name'
		rtn = f"{self.url}/{name}"
		try:
			oldEnv = dict(os.environ)
			os.environ.update(makeGitEnvironment(self))
			try:
				git.Git().ls_remote(rtn)
			finally:
				os.environ.clear()
				os.environ.update(oldEnv)
		except git.GitCommandError as e:
			err = e.stderr
			# Try to strip off the formatting GitCommandError puts on stderr
			match = re.search("stderr: '(.*)'$", err)
			if match:
				err = match.group(1)
			raise RuntimeError(err)
		return rtn

# Patch stashy's AuthenticationException to print the server's message (mostly for issue #16, detecting a captcha check)
def init(self, response, *, oldInit = stashy.errors.AuthenticationException.__init__):
	try:
		Exception.__init__(self, response.json()['errors'][0]['message'].split('\n')[0])
	except Exception:
		oldInit(self, response)
stashy.errors.AuthenticationException.__init__ = init
del init
