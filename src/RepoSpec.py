from .DB import db, registerType

from contextlib import contextmanager
import re

HOST_PATTERN = '[a-zA-Z0-9_-]+'
REPOSPEC_PATTERN = re.compile(f'^(?:({HOST_PATTERN}):)?([a-zA-Z0-9_/.-]+)(?:@([^~^: ]+))?$')

class RepoSpec:
	def __init__(self, name: str, revision: str = None, host: str = None):
		self.name = name.lower()
		self.revision = revision.lower() if revision else None
		self.host = host.lower() if host else None

	@staticmethod
	def fromStr(spec: str) -> 'RepoSpec':
		match = REPOSPEC_PATTERN.match(spec)
		if match:
			return RepoSpec(match.group(2), match.group(3), match.group(1))
		raise ValueError(f"Invalid repospec: {spec}")

	def str(self, includeHost: bool = True, includeRevision: bool = True):
		return (f"{self.host}:" if includeHost and self.host else '') + self.name + (f"@{self.revision}" if includeRevision and self.revision else '')

	def __str__(self):
		return self.str()

	def __eq__(self, o):
		return isinstance(o, RepoSpec) and self.name == o.name and self.revision == o.revision and self.host == o.host

	def __hash__(self):
		return hash(str(self))

	@contextmanager
	def lock(self):
		# This leaves the host out of the lock name because the host isn't always set in the instance
		# This means foo:repo1 and bar:repo1 will share the same lock and might wait on each other unnecessarily, but I'm willing to live with that
		with db.lock(f"repo.{self.str(includeHost = False)}"):
			yield

registerType(RepoSpec, str, RepoSpec.fromStr)
