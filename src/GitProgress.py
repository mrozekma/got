import rich.console
import rich.progress
import sys

from git.util import RemoteProgress

if sys.platform == 'win32':
	# Console.show_cursor() doesn't work on Windows (https://github.com/willmcgugan/rich/issues/75)
	rich.console.Console.show_cursor = lambda *args, **kw: None

class GitProgress(RemoteProgress):
	def __init__(self):
		super().__init__()
		self.progress = rich.progress.Progress(
			"[progress.description]{task.description}",
			rich.progress.BarColumn(None),
			"[progress.percentage]{task.percentage:>3.0f}%",
			# rich.progress.TimeRemainingColumn(),
			"[progress.filesize]{task.fields[msg]}",
		)
		self.currentOpcode = None
		self.task = None

	def update(self, opcode, count, max, msg = None):
		opcodeStrs = {
			self.COUNTING: 'Counting',
			self.COMPRESSING: 'Compressing',
			self.WRITING: 'Writing',
			self.RECEIVING: 'Receiving',
			self.RESOLVING: 'Resolving',
			self.FINDING_SOURCES: 'Finding sources',
			self.CHECKING_OUT: 'Checking out',
		}
		stage, realOpcode = opcode & self.STAGE_MASK, opcode & self.OP_MASK

		try:
			count = int(count)
			max = int(max)
		except ValueError:
			return

		if self.currentOpcode != realOpcode:
			if self.task:
				self.progress.update(self.task, total = 1, completed = 1, msg = '')
			self.currentOpcode = realOpcode
			self.task = self.progress.add_task(opcodeStrs[realOpcode].ljust(15), msg = '')

		if stage & self.BEGIN:
			self.progress.start()
		self.progress.update(self.task, msg = msg or '', total = max, completed = count)

	def finish(self):
		self.progress.stop()

	def __enter__(self):
		self.progress.start()

	def __exit__(self, type, val, tb):
		self.progress.stop()
