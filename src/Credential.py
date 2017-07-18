from .DB import db, ActiveRecord

import keyring
from typing import *

class DBCredential(ActiveRecord):
	def __init__(self, host_name, username, password):
		self.host_name = host_name
		self.username = username
		self.password = password

	@classmethod
	def load(cls, host_name, username):
		# Need to call ActiveRecord.load (a classmethod) while specifying the class.
		# ActiveRecord.load.__func__ is the non-classmethod form of the method
		return ActiveRecord.load.__func__(cls, host_name = host_name, username = username)

	@staticmethod
	def table():
		return 'credential'

class KeyringCredential:
	def __init__(self, host_name, username, password):
		self.host_name = host_name
		self.username = username
		self.password = password

	@staticmethod
	def load(host_name, username):
		return KeyringCredential(host_name, username, keyring.get_password(host_name, username))

	def save(self):
		keyring.set_password(self.host_name, self.username, self.password)

	def delete(self):
		keyring.delete_password(self.host_name, self.username)

# Point the name 'Credential' at the keyring class if a system keyring is available, or the DB class if not
# The DB interface has extra methods since it's an ActiveRecord, but they shouldn't be used since the keyring interface might be active
Credential = DBCredential if isinstance(keyring.get_keyring(), keyring.backends.fail.Keyring) else KeyringCredential
