import json
import os
from pathlib import Path

class DBFile:
	def __init__(self, path, contains, secure = False):
		self.path = path
		self.contains = contains
		self.secure = secure
		try:
			self.data = json.loads(self.path.read_text())
		except FileNotFoundError:
			self.data = {}

	def getJSON(self):
		return json.dumps(self.data)

	def save(self):
		if self.secure and not self.path.is_file():
			self.path.touch(0o600)
		self.path.write_text(self.getJSON())

	def __getitem__(self, k):
		if k not in self.data:
			raise ValueError(f"Unrecognized {self.contains}: {k}")
		return self.data[k]

	def __setitem__(self, k, v):
		self.data[k] = v
		self.save()

	def __delitem__(self, k):
		del self.data[k]
		self.save()

	def __contains__(self, k):
		return k in self.data

	def __iter__(self):
		return iter(self.data)

	def keys(self):
		return self.data.keys()

	def items(self):
		return self.data.items()

class DB:
	def __init__(self, dir = None):
		self.dir = dir or (Path.home() / '.got')
		if not self.dir.is_dir():
			os.mkdir(self.dir)

		self.hosts = DBFile(self.dir / 'hosts.json', 'host')
		self.remotes = DBFile(self.dir / 'remotes.json', 'remote')
		self.clones = DBFile(self.dir / 'clones.json', 'clone')
		self.credentials = DBFile(self.dir / 'credentials.json', 'credential', secure = True)

db = DB()
