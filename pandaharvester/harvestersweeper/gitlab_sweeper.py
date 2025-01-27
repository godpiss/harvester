import os
import shutil

import requests

try:
    import subprocess32 as subprocess
except ImportError:
    import subprocess

from pandaharvester.harvestercore import core_utils
from pandaharvester.harvestermisc.gitlab_utils import get_job_params
from pandaharvester.harvestersweeper.base_sweeper import BaseSweeper

# logger
baseLogger = core_utils.setup_logger("gitlab_sweeper")


# plugin for sweeper with Gitlab
class GitlabSweeper(BaseSweeper):
    # constructor
    def __init__(self, **kwarg):
        self.timeout = 180
        BaseSweeper.__init__(self, **kwarg)

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
        params = get_job_params(workspec)
        url = f"{params['project_api']}/{params['project_id']}/pipelines/{workspec.batchID.split()[0]}/cancel"
        try:
            tmpLog.debug(f"cancel pipeline at {url}")
            r = requests.get(url, headers={"PRIVATE-TOKEN": params["secrets"][params["access_token"]]}, timeout=self.timeout)
            response = r.json()
            tmpLog.debug(f"got {str(response)}")
        except Exception:
            err_str = core_utils.dump_error_message(tmpLog)
            tmpLog.error(err_str)
        tmpLog.debug("done")
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
