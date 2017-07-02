from .DB import ActiveRecord
from .utils import gotRoot

from typing import *

DEFAULT_CONFIG = {
	'clone_root': str(gotRoot / 'repos'),
}

class Config(ActiveRecord):
	def __init__(self, key, value):
		self.key = key
		self.value = value

		if key not in DEFAULT_CONFIG:
			raise ValueError(f"Unrecognized configuration key: {key}")

	@classmethod
	def createTable(cls):
		super().createTable()
		for k, v in DEFAULT_CONFIG.items():
			cls(k, v).save()

# Config is always string keys -> string values, and the key set is fixed, so browsing it as a namespace is convenient
class ConfigInterface:
	def __getattr__(self, k: str) -> str:
		try:
			return Config.load(key = k).value
		except ValueError:
			raise ValueError(f"Unrecognized configuration key: {k}")

	def __setattr__(self, k: str, v: str):
		# Make sure the key exists
		getattr(self, k)
		Config(k, v).save()

	def all(self) -> Iterable[Config]:
		return Config.loadAll()

	def keys(self) -> Iterable[str]:
		for config in self.all():
			yield config.key

config = ConfigInterface()
