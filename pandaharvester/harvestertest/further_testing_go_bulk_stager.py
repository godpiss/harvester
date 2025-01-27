import datetime
import hashlib
import logging
import os
import os.path
import random
import string
import sys
import threading
import time
import uuid

from future.utils import iteritems
from globus_sdk import (
    NativeAppAuthClient,
    RefreshTokenAuthorizer,
    TransferClient,
    TransferData,
)
from pandaharvester.harvesterbody.cacher import Cacher
from pandaharvester.harvesterconfig import harvester_config
from pandaharvester.harvestercore import core_utils
from pandaharvester.harvestercore.communicator_pool import CommunicatorPool
from pandaharvester.harvestercore.db_proxy_pool import DBProxyPool as DBProxy
from pandaharvester.harvestercore.file_spec import FileSpec
from pandaharvester.harvestercore.job_spec import JobSpec
from pandaharvester.harvestercore.plugin_factory import PluginFactory
from pandaharvester.harvestercore.queue_config_mapper import QueueConfigMapper
from pandaharvester.harvestermisc import globus_utils

# initial variables
fileTableName = "file_table"
queueName = "ALCF_Theta"
begin_job_id = 1111
end_job_id = 1113
globus_sleep_time = 15

# connection lock
conLock = threading.Lock()


def dump(obj):
    for attr in dir(obj):
        if hasattr(obj, attr):
            print(f"obj.{attr} = {getattr(obj, attr)}")


if len(sys.argv) > 1:
    queueName = sys.argv[1]
# if len(sys.argv) > 2:
#   begin_job_id = int(sys.argv[2])
# if len(sys.argv) > 3:
#   end_job_id = int(sys.argv[3])
# if len(sys.argv) > 4:
#   globus_sleep_time = int(sys.argv[4])

queueConfigMapper = QueueConfigMapper()
queueConfig = queueConfigMapper.get_queue(queueName)
initial_queueConfig_stager = queueConfig.stager
queueConfig.stager["module"] = "pandaharvester.harvesterstager.go_bulk_stager"
queueConfig.stager["name"] = "GlobusBulkStager"
modified_queueConfig_stager = queueConfig.stager

pluginFactory = PluginFactory()
# get stage-out plugin
stagerCore = pluginFactory.get_plugin(queueConfig.stager)

# logger
_logger = core_utils.setup_logger("further_testing_go_bulk_stager")
tmpLog = core_utils.make_logger(_logger, method_name="further_testing_go_bulk_stager")
tmpLog.debug("start")

for loggerName, loggerObj in logging.Logger.manager.loggerDict.iteritems():
    # print "loggerName - {}".format(loggerName)
    if loggerName.startswith("panda.log"):
        if len(loggerObj.handlers) == 0:
            continue
        if loggerName.split(".")[-1] in ["db_proxy"]:
            continue
        stdoutHandler = logging.StreamHandler(sys.stdout)
        stdoutHandler.setFormatter(loggerObj.handlers[0].formatter)
        loggerObj.addHandler(stdoutHandler)

msgStr = f"plugin={stagerCore.__class__.__name__}"
tmpLog.debug(msgStr)
msgStr = f"Initial queueConfig.stager = {initial_queueConfig_stager}"
tmpLog.debug(msgStr)
msgStr = f"Modified queueConfig.stager = {modified_queueConfig_stager}"
tmpLog.debug(msgStr)

scope = "panda"

proxy = DBProxy()
communicator = CommunicatorPool()
cacher = Cacher(communicator, single_mode=True)
cacher.run()

tmpLog.debug(f"plugin={stagerCore.__class__.__name__}")
tmpLog.debug(f"BasePath from stager configuration: {stagerCore.basePath} ")

# get all jobs in table in a preparing substate
tmpLog.debug("try to get all jobs in a transferring substate")
jobSpec_list = proxy.get_jobs_in_sub_status("transferring", 2000, None, None, None, None, None, None)
tmpLog.debug(f"got {len(jobSpec_list)} jobs")
# loop over all found jobs
if len(jobSpec_list) > 0:
    for jobSpec in jobSpec_list:
        tmpLog.debug(" PandaID = %d status = %s subStatus = %s lockedBy = %s" % (jobSpec.PandaID, jobSpec.status, jobSpec.subStatus, jobSpec.lockedBy))
        # get the transfer groups
        groups = jobSpec.get_groups_of_input_files(skip_ready=True)
        tmpLog.debug(f"jobspec.get_groups_of_input_files() = : {groups}")
        # loop over groups keys to see if db is locked
        for key in groups:
            locked = stagerCore.dbInterface.get_object_lock(key, lock_interval=120)
            if not locked:
                tmpLog.debug("DB Already locked by another thread")
            # now unlock db
            unlocked = stagerCore.dbInterface.release_object_lock(key)
            if unlocked:
                tmpLog.debug("unlocked db")
            else:
                tmpLog.debug(" Could not unlock db")
        # print out jobSpec PandID
        msgStr = f"jobSpec PandaID - {jobSpec.PandaID}"
        tmpLog.debug(msgStr)
        # msgStr = "testing trigger_preparation"
        # tmpLog.debug(msgStr)
        # tmpStat, tmpOut = stagerCore.trigger_preparation(jobSpec)
        # if tmpStat:
        #   msgStr = " OK "
        #   tmpLog.debug(msgStr)
        # elif tmpStat == None:
        #   msgStr = " Temporary failure NG {0}".format(tmpOut)
        #   tmpLog.debug(msgStr)
        # elif not tmpStat:
        #   msgStr = " No Good {0}".format(tmpOut)
        #   tmpLog.debug(msgStr)
        #   sys.exit(1)

        # check status to actually trigger transfer
        # get the files with the group_id and print out
        msgStr = f"Original dummy_transfer_id = {stagerCore.get_dummy_transfer_id()}"
        tmpLog.debug(msgStr)
        # modify dummy_transfer_id from groups of input files
        for key in groups:
            stagerCore.set_dummy_transfer_id_testing(key)
            msgStr = f"Revised dummy_transfer_id = {stagerCore.get_dummy_transfer_id()}"
            tmpLog.debug(msgStr)
            files = proxy.get_files_with_group_id(stagerCore.get_dummy_transfer_id())
            tmpLog.debug(f"Number proxy.get_files_with_group_id(stagerCore.get_dummy_transfer_id()) = {len(files)}")
            files = stagerCore.dbInterface.get_files_with_group_id(stagerCore.get_dummy_transfer_id())
            tmpLog.debug(f"Number stagerCore.dbInterface.get_files_with_group_id(stagerCore.get_dummy_transfer_id()) = {len(files)}")
            msgStr = "checking status for transfer and perhaps ultimately triggering the transfer"
            tmpLog.debug(msgStr)
            tmpStat, tmpOut = stagerCore.check_stage_out_status(jobSpec)
            if tmpStat:
                msgStr = " OK"
                tmpLog.debug(msgStr)
            elif tmpStat is None:
                msgStr = f" Temporary failure No Good {tmpOut}"
                tmpLog.debug(msgStr)
            elif not tmpStat:
                msgStr = f" No Good {tmpOut}"
                tmpLog.debug(msgStr)
