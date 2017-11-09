from .DB import ActiveRecord, Like, db
from .RepoSpec import RepoSpec

from pathlib import Path
from typing import *

class Clone(ActiveRecord):
	def __init__(self, repospec: RepoSpec, path: Path):
		self.repospec = repospec if isinstance(repospec, RepoSpec) else RepoSpec.fromStr(repospec)
		self.path = path if isinstance(path, Path) else Path(path)
		if self.path.exists():
			self.path = self.path.resolve()

	@classmethod
	def loadSpec(cls, repospec: RepoSpec):
		if repospec.host is None:
			ptn = '%:' + str(repospec).replace('\\', '\\\\').replace('%', '\\%')
			yield from Clone.loadAll(repospec = Like(ptn))
		else:
			yield from Clone.loadAll(repospec = repospec)
