import progressbar
import sys

from git.util import RemoteProgress

class Label(progressbar.widgets.FormatWidgetMixin, progressbar.widgets.WidgetBase):
	def __init__(self):
		self.state = ''
		super().__init__(format = '%(state)s (%(value)s of %(max_value)s)')

	def __call__(self, bar, data):
		data['state'] = self.state
		return super().__call__(bar, data)

class GitProgress(RemoteProgress):
	def __init__(self):
		super().__init__()
		self.label = Label()

		# Got messes with sys.stdout and sys.stderr in ways that confuse progressbar and cause it to output on the wrong one
		# This can be worked around by passing in a new stream, but that stream can't be the same instance as sys.stdout or sys.stderr, so I make a new one here that forwards everything
		class StreamWrapper:
			def __getattr__(self, k):
				return getattr(sys.stdout, k)

		self.bar = progressbar.ProgressBar(fd = StreamWrapper(), widgets = [self.label, ' ', progressbar.Bar(), ' ', progressbar.Percentage(), ' '])

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

		self.label.state = opcodeStrs[realOpcode]
		if msg:
			self.label.state += f" ({msg})"
		if stage & self.BEGIN:
			self.bar.start(max)
		else:
			self.bar.max_value = max
		self.bar.update(count, force = bool(msg))

	def finish(self):
		self.bar.finish()
