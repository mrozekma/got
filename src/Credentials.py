from .DB import db

import keyring

class DBKeyring(keyring.backend.KeyringBackend):
	priority = 1

	def get_password(self, service, username):
		if service not in db.credentials:
			raise ValueError(f"No credential found for {service}")
		return db.credentials[service]['password']

	def set_password(self, service, username, password):
		db.credentials[service]['password'] = password
		db.credentials.save()

	def delete_password(self, service, username, password):
		if service in db.credentials:
			db.credentials[service]['password'] = None
			db.credentials.save()

# Actually integrating into the keyring's backend discovery seems impossible; it happens before we get a chance to actually create new classes, and then can't happen again even if requested. Instead we just check if the discovery failed and set DBKeyring as the backend if it did
if isinstance(keyring.get_keyring(), keyring.backends.fail.Keyring):
	keyring.set_keyring(DBKeyring())

class Credentials:
	def __init__(self):
		pass

	def __getitem__(self, service):
		username = db.credentials[service]['username']
		password = keyring.get_password(service, username)
		return username, password

	def __setitem__(self, service, cred):
		username, password = cred
		db.credentials[service] = {'username': username, 'password': None}
		keyring.set_password(service, username, password)

	def __delitem__(self, service):
		username, _ = self[service]
		keyring.delete_password(service, username)
		del db.credentials[service]

	def __contains__(self, service):
		return service in db.credentials

	def __iter__(self):
		return iter(db.credentials)

	def keys(self):
		return db.credentials.keys()

credentials = Credentials()
