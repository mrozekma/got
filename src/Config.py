from .DB import ActiveRecord
from .utils import gotRoot

import os
from pathlib import Path
from typing import *

DEFAULT_CONFIG: Dict[str, Any] = {
	'clone_retries': 0,
	'clone_root': gotRoot / 'repos',
	'default_branch': ':head',
}

def cloneRetriesValidator(v: str):
	try:
		if int(v) < 0:
			raise ValueError("Negative")
	except ValueError:
		raise ValueError("clone_retries must be a non-negative integer")

def cloneRootValidator(v: str) -> str:
	return str(Path(v).resolve())

def defaultBranchValidator(v: str):
	if v.startswith(':') and v not in (':head', ':inherit'):
		raise ValueError(f"Unrecognized default branch: {v}")

CONFIG_VALIDATORS: Dict[str, Callable[[str], Optional[str]]] = {
	'clone_retries': cloneRetriesValidator,
	'clone_root': cloneRootValidator,
	'default_branch': defaultBranchValidator,
}

class Config(ActiveRecord):
	def __init__(self, key, value):
		self.key = key
		self.value = value

		# Worktrees are allowed to stash info in their config list since they're temporary
		if key not in DEFAULT_CONFIG and (not key.startswith('worktree_') or 'GOT_WORKTREE' not in os.environ):
			raise ValueError(f"Unrecognized configuration key: {key}")

	@staticmethod
	def table():
		return 'config'

# Config is always string keys -> string values, and the key set is fixed, so browsing it as a namespace is convenient
class ConfigInterface:
	def __getitem__(self, k: str) -> str:
		try:
			return Config.load(key = k).value
		except ValueError:
			raise ValueError(f"Unrecognized configuration key: {k}")

	def __setitem__(self, k: str, v: str):
		Config(k, v).save()

	def __getattr__(self, k: str) -> str:
		return self[k]

	def __setattr__(self, k: str, v: str):
		self[k] = v

	def all(self) -> Iterable[Config]:
		return Config.loadAll()

	def keys(self) -> Iterable[str]:
		for config in self.all():
			yield config.key

	def ensureDefaults(self):
		# Make sure all the keys exist
		# I don't really like len(DEFAULT_CONFIG) queries getting run on every got execution, so instead of this:

		# for k, v in DEFAULT_CONFIG.items():
		# 	try:
		# 		config[k]
		# 	except ValueError:
		# 		config[k] = v

		# I do this:

		from .DB import db
		db.update(f"INSERT OR IGNORE INTO config VALUES {', '.join('(?, ?)' for _ in DEFAULT_CONFIG)}", *[i for l in DEFAULT_CONFIG.items() for i in l])

config = ConfigInterface()
