import abc
from contextlib import contextmanager
import git
import os
import re
import stashy

from .Credential import Credential
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
	def __init__(self, name, url, username, ssh_key_path = None, clone_url = None):
		self.type = self.getType()
		self.name = name
		self.url = url.rstrip('/')
		self.username = username
		self.ssh_key_path = ssh_key_path
		self.clone_url = clone_url

	# This doesn't implement setting the password because it would need to wait until the host's save() method is called. Changing the password should be done via the Credential interface directly
	@property
	def password(self):
		cred = self.getCredential()
		return cred.password if cred is not None else None

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

	@abc.abstractmethod
	def getType(self = None):
		pass

	@abc.abstractmethod
	def getCloneURL(self, repoName):
		pass

	def check(self):
		pass

class BitbucketHost(SubclassableHost, ActiveRecord):
	def __init__(self, name, url, username, ssh_key_path = None, clone_url = None):
		self._conn = None # Lazy loaded via self.conn property
		super().__init__(name, url, username, ssh_key_path, clone_url)

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
		if self.clone_url is not None:
			return self.getCloneURLFromPattern(name)
		if self.password is None:
			raise RuntimeError("Unable to determine Bitbucket clone URL without authentication")

		try:
			project, repoName = name.split('/')
		except ValueError:
			raise ValueError("Expected repository name of the form <project>/<repository>")

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
			raise RuntimeError("Unable to access Bitbucket project repository list without authentication")
		try:
			return [json['name'] for json in self.conn.projects[project].repos.all()]
		except stashy.errors.NotFoundException as e:
			raise ConnectionError(str(e))
		except stashy.errors.AuthenticationException:
			raise ConnectionError("Invalid/insufficient credentials")

class DaemonHost(SubclassableHost, ActiveRecord):
	def __init__(self, name, url, username, ssh_key_path = None, clone_url = None):
		super().__init__(name, url, username, ssh_key_path, clone_url)

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
