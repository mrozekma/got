// -*- groovy -*-
library 'mrcy'
mrcy.setup()

def build(doCheckout) {
	mrcy.clean()
	doCheckout()
	mrcy.run "${tool 'python3'} -m pip install --user -r test-requirements.txt"

	withCredentials([file(credentialsId: 'got-host-data', variable: 'hostDataFile')]) {
		withCredentials([file(credentialsId: 'bitbucket-key-file', variable: 'sshkey')]) {
			def hostData = readFile hostDataFile
			hostData = hostData.replace('\$SSH_KEY', sshkey.replace('\\', '/'))
			writeFile file: 'host-data.json', text: hostData

			withEnv(['PATH+GOT=.', 'GOT_VERBOSE=2']) {
				mrcy.run "${tool 'python3'} test.py -v -d testrun --host-data host-data.json --junit"
			}
			junit 'testrun/junit.xml'
		}
	}
}

mrcy.reportResult { doCheckout ->
	tasks = [:]
	tasks['Linux'] = {
		node 'linux && tool-python3', {
			build(doCheckout)
		}
	}
	tasks['Windows'] = {
		node 'windows && tool-python3', {
			build(doCheckout)
		}
	}
	parallel tasks
}
