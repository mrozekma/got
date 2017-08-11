from contextlib import contextmanager
import inspect
import os
from pathlib import Path
import psutil
import re
import sqlite3
import sys
import time
import traceback
from typing import *

from .utils import gotRoot, verbose

schemaUpdates = [None]
def schemaUpdate(f):
	schemaUpdates.append(f)
	return f

@schemaUpdate
def v1(db):
	# First sqlite database. Make all the tables
	db.update("CREATE TABLE credentials(host_name text PRIMARY KEY, username text NOT NULL, password text NOT NULL)")
	db.update("CREATE TABLE config(key text PRIMARY KEY, value text NOT NULL)")
	from .Config import DEFAULT_CONFIG
	for k, v in DEFAULT_CONFIG.items():
		db.update("INSERT INTO config VALUES(?, ?)", k, v)
	db.update("CREATE TABLE clones(repospec RepoSpec PRIMARY KEY, path Path NOT NULL)");
	db.update("CREATE TABLE bitbucket_hosts(name text PRIMARY KEY, url text NOT NULL, username text NOT NULL, ssh_key_path text, clone_url text)")
	db.update("CREATE TABLE daemon_hosts(name text PRIMARY KEY, url text NOT NULL, username text NOT NULL, ssh_key_path text, clone_url text)")
	db.update("CREATE TABLE locks(key text PRIMARY KEY, pid int NOT NULL, count int NOT NULL)")

	# Import data from the JSON flat files and archive them
	import json, keyring, shutil, tempfile, zipfile
	useKeyring = not isinstance(keyring.get_keyring(), keyring.backends.fail.Keyring)
	archiveSrcs: Iterable[Path] = set()
	with tempfile.NamedTemporaryFile(delete = False) as f:
		with zipfile.ZipFile(f, 'w') as zip:
			p = gotRoot / 'config.json'
			if p.exists():
				archiveSrcs.add(p)
				data = json.loads(p.read_text())
				for k, v in data.items():
					db.update("UPDATE config SET value = ? WHERE key = ?", v, k)

			p = gotRoot / 'hosts.json'
			if p.exists():
				p2 = gotRoot / 'credentials.json'
				if not p2.exists():
					raise RuntimeError("hosts.json without credentials.json")
				archiveSrcs |= {p, p2}
				data = json.loads(p.read_text())
				data2 = json.loads(p2.read_text())
				for k, v in data.items():
					try:
						cred = data2[k]
						db.update(f"INSERT INTO {v['type']}_hosts VALUES(?, ?, ?, ?, ?)", k, v['url'], cred['username'], None, None)
						if useKeyring:
							keyring.set_password(k, cred['username'], cred['password'])
						else:
							db.update("INSERT INTO credentials VALUES(?, ?, ?)", k, cred['username'], cred['password'])
					except KeyError:
						raise RuntimeError(f"Host {k} has no stored credential")

			p = gotRoot / 'clones.json'
			if p.exists():
				archiveSrcs.add(p)
				data = json.loads(p.read_text())
				for k, v in data.items():
					db.update("INSERT INTO clones VALUES(?, ?)", k, v)

			for src in archiveSrcs:
				zip.write(str(src), os.path.basename(src))
	if archiveSrcs:
		archivePath = gotRoot / 'old_database.zip'
		shutil.move(f.name, archivePath)
		for p in archiveSrcs:
			p.unlink()
		if verbose(2):
			print(f"Imported old JSON database. Archived files at {archivePath.resolve()}", file = sys.stderr)
	else:
		os.unlink(f.name)

def savepointNameGenerator():
	i = 1
	while True:
		yield f"savepoint_{i}"
		i += 1
savepointNameGenerator = savepointNameGenerator()

class DB:
	def __init__(self, dir: Path):
		os.makedirs(dir, exist_ok = True)
		self.path = dir / 'db'
		self.conn = sqlite3.connect(str(self.path), isolation_level = None)
		self.conn.row_factory = sqlite3.Row
		self.schemaUpdates()

	def close(self):
		self.conn.close()

	def schemaUpdates(self):
		with self.transaction(True):
			version = startVersion = next(self.select("PRAGMA user_version"))['user_version']
			for version, updater in enumerate(schemaUpdates[startVersion+1:], startVersion + 1):
				try:
					updater(self)
				except:
					raise RuntimeError(f"Failed to update database to version {version}")
			if version == startVersion:
				return False
			self.update(f"PRAGMA user_version = {version}")
			if verbose(2):
				if startVersion == 0:
					print("New database created at %s" % self.path, file = sys.stderr)
				else:
					print(f"Database updated to version {version}")
			return True

	@contextmanager
	def attachDatabase(self, name: str, db: Union[Path, 'DB']):
		if isinstance(db, DB):
			db = db.path
		self.update("ATTACH DATABASE ? as ?", db, name)
		try:
			yield
		finally:
			self.update("DETACH DATABASE ?", name)

	def worktreeSetup(self, parent: Union[Path, 'DB']):
		with self.attachDatabase('parent', parent):
			# Might want to include some rows from 'config' in the future, but currently the only one is 'clone_root', which we don't want
			for table in ('credentials', 'bitbucket_hosts', 'daemon_hosts'):
				self.update("INSERT INTO main.%s SELECT * FROM parent.%s" % (table, table))

	def importRepos(self, source: Union[Path, 'DB'], patterns: List[str]):
		with self.attachDatabase('source', source):
			placeholders = ["repospec LIKE ?" for _ in patterns]
			vals = [repo.replace('%', '\\%').replace('*', '%') for repo in patterns]
			vals = [val if ':' in val else f"%:{val}" for val in vals]
			self.update(f"INSERT OR IGNORE INTO main.clones SELECT * from source.clones WHERE {' OR '.join(placeholders)} ESCAPE '\\'", *vals)

	@contextmanager
	def transaction(self, exclusive = False):
		savepointName = next(savepointNameGenerator)
		oldLevel = self.conn.isolation_level # This is almost certainly None, which is auto-commit mode
		self.conn.isolation_level = 'EXCLUSIVE' if exclusive else '' # Empty string is regular transactional mode
		try:
			# pysqlite's need to automate certain transaction operations really screws up DDL instructions.
			# Savepoints seem to avoid the problem
			# with self.conn:
			self.conn.execute(f"SAVEPOINT {savepointName}")
			yield
			self.conn.execute(f"RELEASE SAVEPOINT {savepointName}")
		except:
			self.conn.execute(f"ROLLBACK TO SAVEPOINT {savepointName}")
			raise
		finally:
			self.conn.isolation_level = oldLevel

	@contextmanager
	def lock(self, key, timeout = None, reentrant = True):
		pid = os.getpid()
		tries = 0
		while True:
			with self.transaction(True):
				owner = None
				for row in self.selectRow("SELECT pid FROM locks WHERE key = ?", key):
					if psutil.pid_exists(row['pid']):
						owner = row['pid']
				if owner is None:
					if verbose(3):
						print(f"Acquired lock `{key}'", file = sys.stderr)
					self.update("INSERT OR REPLACE INTO locks(key, pid, count) VALUES(?, ?, 1)", key, pid)
					break
				elif reentrant and owner == pid:
					if verbose(3):
						print(f"Re-entered already owned lock `{key}'", file = sys.stderr)
					self.update("UPDATE locks SET count = count + 1 WHERE key = ? AND pid = ?", key, pid)
					break
			if verbose(3):
				print(f"Failed to acquired lock `{key}': held by {owner}", file = sys.stderr)
			if timeout is not None and tries >= timeout:
				raise TimeoutError(f"Unable to acquire lock ({key} held by {owner})")
			tries += 1
			if tries == 3 and verbose(1):
				try:
					proc = psutil.Process(owner)
					parent = proc.parent()
					print(f"Waiting for lock... (held by {owner}, run by {parent.pid} ({parent.name()}))", file = sys.stderr)
				except Exception: # 'owner' might have ended, or be restricted, or its parent might be unavailable
					print(f"Waiting for lock... (held by {owner})", file = sys.stderr)
			time.sleep(1)

		try:
			# We have the lock; run caller body
			yield
		finally:
			# Release the lock
			self.update("UPDATE locks SET count = count - 1 WHERE key = ? AND pid = ?", key, pid)
			self.update("DELETE FROM locks WHERE key = ? AND pid = ? AND count = 0", key, pid)
			if verbose(3):
				print(f"Released lock `{key}'", file = sys.stderr)

	@contextmanager
	def cursor(self, expr = None, *args) -> sqlite3.Cursor:
		if verbose(3):
			pargs = [str(arg) for arg in args]
			stack = [f"{os.path.basename(frame.filename)}:{frame.lineno} {frame.name}" for frame in traceback.extract_stack(limit = 15)]
			width = max(len(expr or ''), *[len(i) for i in pargs], *[len(i) for i in stack])

			print(file = sys.stderr)
			print(u'\u250c' + u'\u2500' * (width + 2) + u'\u2510', file = sys.stderr)
			print(u"\u2502 %*s \u2502" % (-width, expr or ''), file = sys.stderr)
			if pargs:
				print(u'\u251c' + u'\u2500' * (width + 2) + u'\u2524', file = sys.stderr)
				for arg in pargs:
					print(u"\u2502 %*s \u2502" % (-width, arg), file = sys.stderr)
				print(u'\u251c' + u'\u2500' * (width + 2) + u'\u2524', file = sys.stderr)
			else:
				print(u'\u255e' + u'\u2550' * (width + 2) + u'\u2561', file = sys.stderr)
			for frame in stack:
				print(u"\u2502 %*s \u2502" % (-width, frame), file = sys.stderr)
			print(u'\u2514' + u'\u2500' * (width + 2) + u'\u2518', file = sys.stderr)
			print(file=sys.stderr)

		cur = self.conn.cursor()
		try:
			if expr:
				cur.execute(expr, args)
			yield cur
		finally:
			cur.close()

	def selectRow(self, expr, *args):
		with self.cursor(expr, *args) as cur:
			for row in cur:
				yield row

	def select(self, expr, *args):
		for row in self.selectRow(expr, *args):
			yield {k: row[k] for k in row.keys()}

	def matches(self, expr, *args):
		with self.cursor(expr, *args) as cur:
			return bool(cur.fetchone())

	def update(self, expr, *args):
		with self.cursor(expr, *args):
			pass

class Like:
	def __init__(self, v):
		self.v = v

class ActiveRecord:
	registeredTypes = set()

	@classmethod
	def __init_subclass__(cls):
		super().__init_subclass__()

	@classmethod
	def table(cls):
		# https://stackoverflow.com/a/12867228/309308
		return re.sub('((?<=[a-z0-9])[A-Z]|(?!^)[A-Z](?=[a-z]))', r'_\1', cls.__name__).lower() + 's'

	@classmethod
	def fields(cls):
		return inspect.getfullargspec(cls.__init__).args[1:]

	@classmethod
	def pks(cls):
		return cls.fields()[:1]

	@classmethod
	def count(cls):
		for row in db.select(f"SELECT COUNT(*) FROM {cls.table()}"):
			return row['COUNT(*)']

	@classmethod
	def load(cls, *, err = None, **attrs):
		try:
			return next(cls.loadAll(**attrs))
		except StopIteration:
			raise ValueError(err or f"{cls} database lookup failed") from None

	@classmethod
	def tryLoad(cls, **attrs):
		try:
			return cls.load(**attrs)
		except ValueError:
			return None

	@staticmethod
	def makeClause(attrs):
		if not attrs:
			return '', []

		# Need to construct the clause 'WHERE k1 = ? AND k2 = ? AND k3 = ? ...', and return it along with (v1, v2, v3, ...)
		placeholders, vals, hasPatterns = [], [], False
		for k, v in attrs.items():
			if v is None:
				placeholders.append(f"{k} is NULL")
			elif isinstance(v, Like):
				placeholders.append(f"{k} LIKE ?")
				vals.append(v.v)
				hasPatterns = True
			else:
				placeholders.append(f"{k} = ?")
				vals.append(v)
		return ' WHERE ' + ' AND '.join(placeholders) + (" ESCAPE '\\'" if hasPatterns else ''), vals

	@classmethod
	def loadAll(cls, *, sort = None, **attrs):
		clause, vals = ActiveRecord.makeClause(attrs)
		query = 'SELECT * FROM ' + cls.table() + clause
		if sort is not None:
			query += ' ORDER BY ' + sort
		for row in db.select(query, *vals):
			yield cls(**row)

	@classmethod
	def deleteAll(cls, **attrs):
		clause, vals = ActiveRecord.makeClause(attrs)
		expr = 'DELETE FROM ' + cls.table() + clause
		with db.cursor(expr, *vals) as cur:
			return cur.rowcount

	def save(self):
		cls = self.__class__
		fields = cls.fields()
		placeholders = ['?'] * len(fields)
		vals = [getattr(self, field) for field in fields]
		db.update(f"INSERT OR REPLACE INTO {cls.table()}({', '.join(fields)}) VALUES ({', '.join(placeholders)})", *vals)

	def delete(self):
		cls = self.__class__
		fields = cls.fields()
		clauses, vals = [], []
		for field in fields:
			val = getattr(self, field)
			if val is None:
				clauses.append(f"{field} IS NULL")
			else:
				clauses.append(f"{field} = ?")
				vals.append(val)
		db.update(f"DELETE FROM {cls.table()} WHERE {' AND '.join(clauses)}", *vals)

T = TypeVar('T')
def registerType(cls: type, pyToDb: Callable[[ActiveRecord], T], dbToPy: Callable[[T], ActiveRecord]):
	ActiveRecord.registeredTypes.add(cls.__name__)
	sqlite3.register_adapter(cls, pyToDb)
	sqlite3.register_converter(cls.__name__, dbToPy)

# Got types are registered in their class files. Built-in types are registered here
from pathlib import PosixPath, WindowsPath
for cls in (Path, PosixPath, WindowsPath):
	registerType(cls, str, Path)

db = DB(gotRoot)
