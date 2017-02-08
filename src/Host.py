import abc
import git
import re
import requests

from .Credentials import credentials
from .DB import db

class Host(abc.ABC):
	def __new__(self, name, type, *args, **kw):
		if type == 'bitbucket':
			rtn = BitbucketHost(*args, **kw)
		elif type == 'daemon':
			rtn = DaemonHost(*args, **kw)
		else:
			raise ValueError(f"Unknown type `{type}'")
		rtn.name = name
		return rtn

	@abc.abstractmethod
	def getCloneURL(self, repoName):
		pass

	@staticmethod
	def fromDB(name):
		kw = {}
		kw.update(db.hosts[name])
		if name in credentials:
			kw['username'], kw['password'] = credentials[name]
		return Host(name, **kw)

class BitbucketHost:
	def __init__(self, url, username, password):
		self.url = url.rstrip('/')
		self.username = username
		self.password = password

		# Test connection
		self.api('application-properties')

	def api(self, route):
		try:
			resp = requests.get(f"{self.url}/rest/api/1.0/{route}", auth = (self.username, self.password))
		except requests.exceptions.ConnectionError:
			raise ConnectionError("Unable to connect to Bitbucket")
		if resp.status_code != 200:
			if resp.status_code == 404:
				try:
					# Try to get an error message out of the response
					msg = resp.json()['errors'][0]['message'].rstrip('.')
				except Exception:
					# Generic error
					msg = "Invalid route"
				raise ConnectionError(msg) from None
			elif resp.status_code == 401:
				raise ConnectionError("Invalid/insufficient credentials")
			else:
				raise ConnectionError(f"Unexpected status code {resp.status_code}")
		return resp.json()

	def getCloneURL(self, repoPath):
		try:
			project, repoName = repoPath.split('/')
		except ValueError:
			raise ValueError("Expected repository name of the form <project>/<repository>")
		data = self.api(f"projects/{project}/repos/{repoName}")
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

class DaemonHost:
	def __init__(self, url, username, password):
		self.url = url.rstrip('/')
		self.username = username
		self.password = password

	def getCloneURL(self, name):
		# Nothing stops 'name' from escaping the path specified by self.url, like '../../../foo'. I can't see a problem with allowing it other than that it's weird, and allowing normal subdirectory traversal could be useful, so not currently putting any restrictions on 'name'
		rtn = f"{self.url}/{name}"
		try:
			git.Git().ls_remote(rtn)
		except git.GitCommandError as e:
			err = e.stderr
			# Try to strip off the formatting GitCommandError puts on stderr
			match = re.search("stderr: '(.*)'$", err)
			if match:
				err = match.group(1)
			raise RuntimeError(err)
		return rtn
