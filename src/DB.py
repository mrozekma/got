'''
Operations:

keys
values (?)
get
set
del
iter
in

hosts.getJSON

config: keys, get, iter, in, set
hosts: keys, getJSON, in, get, set, del
remotes: 
clones: keys, get, set, del, iter, values
credentials: 
'''

from contextlib import contextmanager
import inspect
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import traceback
from typing import *

from .utils import gotRoot, verbose

'''
		self.config = DBFile(self.dir / 'config.json', 'config key', defaultData = DEFAULT_CONFIG)
		self.hosts = DBFile(self.dir / 'hosts.json', 'host')
		self.remotes = DBFile(self.dir / 'remotes.json', 'remote')
		self.clones = DBFile(self.dir / 'clones.json', 'clone')
		self.credentials = DBFile(self.dir / 'credentials.json', 'credential', secure = True)
'''

class DB:
	def __init__(self, path):
		self.path = path
		self.isNew = not path.exists()
		self.conn = sqlite3.connect(str(path))
		self.conn.row_factory = sqlite3.Row
		self.conn.isolation_level = None

		if self.isNew:
			self.update("PRAGMA user_version = 1")

	@contextmanager
	def transaction(self, exclusive = False):
		oldLevel = self.conn.isolation_level # This is almost certainly None, which is auto-commit mode
		self.conn.isolation_level = 'EXCLUSIVE' if exclusive else '' # Empty string is regular transactional mode
		try:
			with self.conn:
				yield
		finally:
			self.conn.isolation_level = oldLevel

	@contextmanager
	def cursor(self, expr = None, *args) -> sqlite3.Cursor:
		if verbose(3):
			pargs = [str(arg) for arg in args]
			stack = [f"{os.path.basename(frame.filename)}:{frame.lineno} {frame.name}" for frame in traceback.extract_stack(limit = 10)]
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

class ActiveRecord:
	registeredTypes = set()

	@classmethod
	def __init_subclass__(cls):
		super().__init_subclass__()
		if db.isNew:
			cls.createTable()

	@classmethod
	def createTable(cls):
		pks = cls.pks()
		argspec = inspect.getfullargspec(cls.__init__)

		cols = []
		for col in cls.fields():
			colType = 'text'
			if col in argspec.annotations:
				typ = argspec.annotations[col]
				if typ.__name__ in ActiveRecord.registeredTypes:
					colType = typ.__name__
			cols.append(f"{col} {colType}{' PRIMARY KEY' if col in pks else ''}")
		db.update(f"CREATE TABLE {cls.table()}({', '.join(cols)})")

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

	@classmethod
	def loadAll(cls, *, sort = None, **attrs):
		query = 'SELECT * FROM ' + cls.table()
		if attrs:
			# Need to construct the clause 'WHERE k1 = ? AND k2 = ? AND k3 = ? ...', and pass (v1, v2, v3, ...) separately
			placeholders, vals = zip(*((f"{k} = ?", v) for k, v in attrs.items()))
			query += ' WHERE ' + ' AND '.join(placeholders)
		else:
			vals = ()
		if sort is not None:
			query += ' ORDER BY ' + sort
		for row in db.select(query, *vals):
			yield cls(**row)

	def save(self):
		cls = self.__class__
		fields = cls.fields()
		placeholders = ['?'] * len(fields)
		vals = [getattr(self, field) for field in fields]
		db.update(f"INSERT OR REPLACE INTO {cls.table()}({', '.join(fields)}) VALUES ({', '.join(placeholders)})", *vals)

	def delete(self):
		cls = self.__class__
		fields = cls.fields()
		clauses = [f"{field} = ?" for field in fields]
		vals = [getattr(self, field) for field in fields]
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

db = DB(gotRoot / 'db')

### Test
#TODO Rm
'''
class Test(ActiveRecord):
	def __init__(self, foo, bar, baz):
		self.foo = foo
		self.bar = bar
		self.baz = baz

print(list(Test.loadAll(foo = 'foo')))
Test('foo', 'bar', 'baz').save()
Test('foo2', 'bar2', 'baz').save()
Test('foo3', 'bar', 'baz3').save()
'''
