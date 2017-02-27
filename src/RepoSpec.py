import re

HOST_PATTERN = '[a-zA-Z0-9_-]+'
REPOSPEC_PATTERN = re.compile(f'^(?:({HOST_PATTERN}):)?([a-zA-Z0-9_/.-]+)(?:@([0-9a-fA-F]{{6,40}}))?$')

class RepoSpec:
	def __init__(self, name, revision = None, host = None):
		self.name = name.lower()
		self.revision = revision.lower() if revision else None
		self.host = host.lower() if host else None

	@staticmethod
	def fromStr(spec):
		match = REPOSPEC_PATTERN.match(spec)
		if match:
			return RepoSpec(match.group(2), match.group(3), match.group(1))
		raise ValueError(f"Invalid repo spec: {spec}")

	def __str__(self):
		return (f"{self.host}:" if self.host else '') + self.name + (f"@{self.revision}" if self.revision else '')

	def __eq__(self, o):
		return isinstance(o, RepoSpec) and self.name == o.name and self.revision == o.revision and self.host == o.host

	def __hash__(self):
		return hash(str(self))
