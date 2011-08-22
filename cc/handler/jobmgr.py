
import os, subprocess, select, fcntl, signal

from zmq.eventloop.ioloop import PeriodicCallback
from cc.crypto import CryptoContext
from cc.handler import CCHandler
from cc.message import CCMessage
from cc.reqs import ErrorMessage, JobConfigReplyMessage
from cc.util import set_nonblocking

import skytools

__all__ = ['JobMgr']

CC_HANDLER = 'JobMgr'

#
# JobMgr
#

class JobState:
    def __init__(self, jname, jcf, log, cc_url, ioloop, pidfiledir, xtx):
        self.jname = jname
        self.jcf = jcf
        self.proc = None
        self.log = log
        self.cc_url = cc_url
        self.timer = None
        self.ioloop = ioloop
        self.pidfile = "%s/%s.pid" % (pidfiledir, self.jname)
        self.cfdict = {
                'job_name': self.jname,
                'pidfile': self.pidfile,
        }
        xtx.fill_config(self.cfdict)
        for o in self.jcf.options():
            self.cfdict[o] = self.jcf.get(o)

    def handle_timer(self):
        if self.proc:
            self.log.info('JobState.handle_timer')
            data = self.proc.stdout.read()
            if data:
                self.log.info('handle_timer: stdout=%s', repr(data.strip()))
            if self.proc.poll() is not None:
                x = self.proc.wait()
                self.log.info('handle_timer: proc exited with %s', repr(x))
                self.proc = None
        else:
            # daemonization successful?
            live = skytools.signal_pidfile(self.pidfile, 0)
            if live:
                self.log.debug('handle_timer: %s is alive', self.jname)
            else:
                self.log.info('handle_timer: %s is dead', self.jname)

    def start(self):
        # unsure about the best way to specify target
        mod = self.jcf.get('module', '')
        script = self.jcf.get('module', '')
        cls = self.jcf.get('class', '')
        args = ['-d', '--cc', self.cc_url, '--ccdaemon', self.jname]
        if mod:
            cmd = ['python', '-m', mod] + args
        elif script:
            cmd = [script] + args
        else:
            raise skytools.UsageError('dunno how to launch class')

        self.log.info('Launching %s: %s', self.jname, " ".join(cmd))
        self.proc = subprocess.Popen(cmd, close_fds = True,
                                stdin = open(os.devnull, 'rb'),
                                stdout = subprocess.PIPE,
                                stderr = subprocess.STDOUT)

        set_nonblocking(self.proc.stdout, True)

        self.timer = PeriodicCallback(self.handle_timer, 2*1000, self.ioloop)
        self.timer.start()

    def stop(self):
        try:
            self.log.info('Killing %s', self.jname)
            skytools.signal_pidfile(self.pidfile, signal.SIGINT)
        except:
            self.log.exception('signal_pidfile failed')


class JobMgr(CCHandler):
    """Provide config to local daemons / tasks."""

    CC_ROLES = ['local']

    def __init__(self, hname, hcf, ccscript):
        super(JobMgr, self).__init__(hname, hcf, ccscript)

        self.local_url = ccscript.local_url
        self.pidfiledir = hcf.getfile('pidfiledir', '~/pid')

        self.jobs = {}
        for dname in self.cf.getlist('daemons'):
            self.add_job(dname)

        self.xtx = CryptoContext(None, self.log)

    def add_job(self, jname):
        jcf = skytools.Config(jname, self.cf.filename, ignore_defs = True)
        jstate = JobState(jname, jcf, self.log, self.local_url, self.ioloop, self.pidfiledir, self.xtx)
        self.jobs[jname] = jstate
        jstate.start()

    def handle_msg(self, cmsg):
        """Got message from client, send to remote CC"""

        self.log.info('JobMgr req: %s', cmsg)
        data = cmsg.get_payload(self.xtx)
        if not data:
            return

        if data.req == 'job.config':
            if not hasattr (data, 'job_name'):
                msg = ErrorMessage(
                    req = data.req,
                    msg = "Missing job_name")
            elif not data.job_name in self.jobs:
                msg = ErrorMessage(
                    req = data.req,
                    job_name = data.job_name,
                    msg = "Unknown job_name")
            else:
                job = self.jobs[data.job_name]
                msg = JobConfigReplyMessage(
                    req = data.req,
                    job_name = data.job_name,
                    config = job.cfdict)
        else:
            msg = ErrorMessage(
                req = data.req,
                #job_name = data.job_name,
                msg = 'Unsupported req')
        crep = self.xtx.create_cmsg(msg)
        crep.take_route(cmsg)
        self.cclocal.send_cmsg(crep)
        self.log.info('JobMgr answer: %s', crep)

    def stop(self):
        for j in self.jobs.values():
            j.stop()

