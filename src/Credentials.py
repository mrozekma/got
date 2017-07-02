from .DB import db, ActiveRecord

import keyring
from typing import *

#TODO More secure password storage
class Credential(ActiveRecord):
	def __init__(self, service, username, password):
		self.service = service
		self.username = username
		self.password = password

class DBKeyring(keyring.backend.KeyringBackend):
	priority = 1

	def get_password(self, service, username):
		return Credential.load(service = service, username = username).password

	def set_password(self, service, username, password):
		cred = Credential.load(service = service, username = username)
		cred.password = password
		cred.save()

	def delete_password(self, service, username):
		self.set_password(service, username, None)

# Actually integrating into the keyring's backend discovery seems impossible; it happens before we get a chance to actually create new classes, and then can't happen again even if requested. Instead we just check if the discovery failed and set DBKeyring as the backend if it did
if isinstance(keyring.get_keyring(), keyring.backends.fail.Keyring):
	keyring.set_keyring(DBKeyring())

#TODO Rm this
#TODO Rename file
'''
class Credentials:
	def __init__(self):
		pass

	def __getitem__(self, service: str) -> Tuple[str, str]:
		username = db.credentials[service]['username']
		password = keyring.get_password(service, username)
		return username, password

	def __setitem__(self, service: str, cred: Tuple[str, str]) -> None:
		username, password = cred
		db.credentials[service] = {'username': username, 'password': None}
		keyring.set_password(service, username, password)

	def __delitem__(self, service: str) -> None:
		username, _ = self[service]
		keyring.delete_password(service, username)
		del db.credentials[service]

	def __contains__(self, service: str) -> bool:
		return service in db.credentials

	def __iter__(self):
		return iter(db.credentials)

	def keys(self):
		return db.credentials.keys()

credentials = Credentials()
'''
