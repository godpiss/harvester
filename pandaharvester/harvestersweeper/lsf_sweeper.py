import os
import shutil

try:
    import subprocess32 as subprocess
except BaseException:
    import subprocess

from pandaharvester.harvestercore import core_utils
from pandaharvester.harvestercore.plugin_base import PluginBase

# logger
baseLogger = core_utils.setup_logger("lsf_sweeper")


# plugin for sweeper with LSF
class LSFSweeper(PluginBase):
    # constructor
    def __init__(self, **kwarg):
        PluginBase.__init__(self, **kwarg)

    # kill a worker
    def kill_worker(self, workspec):
        """Kill a worker in a scheduling system like batch systems and computing elements.

        :param workspec: worker specification
        :type workspec: WorkSpec
        :return: A tuple of return code (True for success, False otherwise) and error dialog
        :rtype: (bool, string)
        """
        # make logger
        tmpLog = self.make_logger(baseLogger, f"workerID={workspec.workerID}", method_name="kill_worker")
        # kill command
        comStr = f"bkill {workspec.batchID}"
        # execute
        p = subprocess.Popen(comStr.split(), shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdOut, stdErr = p.communicate()
        retCode = p.returncode
        if retCode != 0:
            # failed
            errStr = f'command "{comStr}" failed, retCode={retCode}, error: {stdOut} {stdErr}'
            tmpLog.error(errStr)
            return False, errStr
        else:
            tmpLog.info(f"Succeeded to kill workerID={workspec.workerID} batchID={workspec.workerID}")
        # return
        return True, ""

    # cleanup for a worker
    def sweep_worker(self, workspec):
        """Perform cleanup procedures for a worker, such as deletion of work directory.

        :param workspec: worker specification
        :type workspec: WorkSpec
        :return: A tuple of return code (True for success, False otherwise) and error dialog
        :rtype: (bool, string)
        """
        # make logger
        tmpLog = self.make_logger(baseLogger, f"workerID={workspec.workerID}", method_name="sweep_worker")
        # clean up worker directory
        if os.path.exists(workspec.accessPoint):
            shutil.rmtree(workspec.accessPoint)
            tmpLog.info(f"removed {workspec.accessPoint}")
        else:
            tmpLog.info("access point already removed.")
        # return
        return True, ""
