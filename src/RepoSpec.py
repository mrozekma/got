import re

HOST_PATTERN = '[a-zA-Z0-9_-]+'
REPOSPEC_PATTERN = re.compile(f'^(?:({HOST_PATTERN}):)?([a-zA-Z0-9_/-]+)(?:@([0-9a-fA-F]{{6,40}}))?$')

class RepoSpec:
	def __init__(self, name, revision = None, host = None):
		self.name = name
		self.revision = revision
		self.host = host

	@staticmethod
	def fromStr(spec):
		match = REPOSPEC_PATTERN.match(spec)
		if match:
			return RepoSpec(match.group(2), match.group(3), match.group(1))
		raise ValueError(f"Invalid repo spec: {spec}")

	def __str__(self):
		return (f"{self.host}:" if self.host else '') + self.name + (f"@{self.revision}" if self.revision else '')
