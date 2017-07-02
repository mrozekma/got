from .DB import ActiveRecord, db
from .RepoSpec import RepoSpec

from pathlib import Path
from typing import *

class Clone(ActiveRecord):
	def __init__(self, repospec: RepoSpec, path: Path):
		self.repospec = repospec if isinstance(repospec, RepoSpec) else RepoSpec.fromStr(repospec)
		self.path = path if isinstance(path, Path) else Path(path)

	@classmethod
	def loadSpec(cls, repospec: RepoSpec):
		if repospec.host is None:
			ptn = '%:' + str(repospec).replace('\\', '\\\\').replace('%', '\\%')
			rows = db.select(f"SELECT * FROM {cls.table()} WHERE repospec LIKE ? ESCAPE '\\'", ptn)
		else:
			rows = db.select(f"SELECT * FROM {cls.table()} WHERE repospec = ?", repospec)
		for row in rows:
			yield Clone(**row)
