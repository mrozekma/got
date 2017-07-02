import abc
import git
import os
import re
import stashy

#from .Credentials import credentials
from .DB import ActiveRecord
from .utils import makeGitEnvironment

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
	def __init__(self):
		self.type = self.getType()

	@classmethod
	def __init_subclass__(cls):
		super().__init_subclass__()
		Host.subclasses[cls.getType()] = cls

	@abc.abstractmethod
	def getType(self = None):
		pass

	@abc.abstractmethod
	def getCloneURL(self, repoName):
		pass

	def check(self):
		pass

#TODO Rm this
	'''
	@staticmethod
	def fromDB(name):
		kw = {}
		try:
			kw.update(db.hosts[name])
		except KeyError:
			raise ValueError(f"No host named {name}")
		if name in credentials:
			kw['username'], kw['password'] = credentials[name]
		return Host(name, **kw)
	'''

class BitbucketHost(SubclassableHost, ActiveRecord):
	def __init__(self, name, url, username, password):
		super().__init__()
		self._conn = None # Lazy loaded via self.conn property
		self.name = name
		self.url = url.rstrip('/')
		self.username = username
		self.password = password

	@property
	def conn(self):
		if self._conn is None:
			self._conn = stashy.connect(self.url, self.username, self.password)
		return self._conn

	def __setattr__(self, k, v):
		super().__setattr__(k, v)
		# Invalidate connection if any of the connection parameters have changed
		if k in ('url', 'username', 'password'):
			self._conn = None

	def check(self):
		# Test connection
		try:
			self.conn.projects.list()
		except stashy.errors.NotFoundException:
			raise ConnectionError("Unable to connect to Bitbucket")
		except stashy.errors.AuthenticationException as e:
			raise ConnectionError(str(e))

	def getType(self = None):
		return 'bitbucket'

	def getCloneURL(self, repoPath):
		try:
			project, repoName = repoPath.split('/')
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
		# Not sure if one protocol should be favored over another. Going with HTTP at the moment if available, otherwise taking the first URL listed
		rtn = None
		for clone in data['links']['clone']:
			if rtn is None or clone['name'] == 'http':
				rtn = clone['href']
		if rtn is None:
			raise ValueError("No clone links found")
		return rtn

	def getReposInProject(self, project):
		try:
			return [json['name'] for json in self.conn.projects[project].repos.all()]
		except stashy.errors.NotFoundException as e:
			raise ConnectionError(str(e))
		except stashy.errors.AuthenticationException:
			raise ConnectionError("Invalid/insufficient credentials")

class DaemonHost(SubclassableHost, ActiveRecord):
	def __init__(self, name, url, username, password):
		super().__init__()
		self.name = name
		self.url = url.rstrip('/')
		self.username = username
		self.password = password

	def getType(self = None):
		return 'daemon'

	def getCloneURL(self, name):
		# Nothing stops 'name' from escaping the path specified by self.url, like '../../../foo'. I can't see a problem with allowing it other than that it's weird, and allowing normal subdirectory traversal could be useful, so not currently putting any restrictions on 'name'
		rtn = f"{self.url}/{name}"
		try:
			oldEnv = dict(os.environ)
			os.environ.update(makeGitEnvironment(self.name))
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
