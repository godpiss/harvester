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


def dump(obj):
    for attr in dir(obj):
        if hasattr(obj, attr):
            print(f"obj.{attr} = {getattr(obj, attr)}")


print(len(sys.argv))
queueName = "ALCF_Theta"
job_id = 1111
globus_sleep_time = 15

if len(sys.argv) > 1:
    queueName = sys.argv[1]
if len(sys.argv) > 2:
    job_id = int(sys.argv[2])
if len(sys.argv) > 3:
    globus_sleep_time = int(sys.argv[3])


queueConfigMapper = QueueConfigMapper()
queueConfig = queueConfigMapper.get_queue(queueName)
initial_queueConfig_preparator = queueConfig.preparator
queueConfig.preparator["module"] = "pandaharvester.harvesterpreparator.go_preparator"
queueConfig.preparator["name"] = "GoPreparator"
modified_queueConfig_preparator = queueConfig.preparator

pluginFactory = PluginFactory()
# get stage-out plugin
preparatorCore = pluginFactory.get_plugin(queueConfig.preparator)

# logger
_logger = core_utils.setup_logger("stageInTest_go_preparator")
tmpLog = core_utils.make_logger(_logger, method_name="stageInTest_go_preparator")
tmpLog.debug("start")

for loggerName, loggerObj in iteritems(logging.Logger.manager.loggerDict):
    # print "loggerName - {}".format(loggerName)
    if loggerName.startswith("panda.log"):
        if len(loggerObj.handlers) == 0:
            continue
        if loggerName.split(".")[-1] in ["db_proxy"]:
            continue
        stdoutHandler = logging.StreamHandler(sys.stdout)
        stdoutHandler.setFormatter(loggerObj.handlers[0].formatter)
        loggerObj.addHandler(stdoutHandler)

msgStr = f"plugin={preparatorCore.__class__.__name__}"
tmpLog.debug(msgStr)
msgStr = f"Initial queueConfig.preparator = {initial_queueConfig_preparator}"
tmpLog.debug(msgStr)
msgStr = f"Modified queueConfig.preparator = {modified_queueConfig_preparator}"
tmpLog.debug(msgStr)

scope = "panda"

proxy = DBProxy()
communicator = CommunicatorPool()
cacher = Cacher(communicator, single_mode=True)
cacher.run()

Globus_srcPath = queueConfig.preparator["Globus_srcPath"]
srcEndpoint = queueConfig.preparator["srcEndpoint"]
basePath = queueConfig.preparator["basePath"]
Globus_dstPath = queueConfig.preparator["Globus_dstPath"]
dstEndpoint = queueConfig.preparator["dstEndpoint"]

# need to get client_id and refresh_token from PanDA server via harvester cache mechanism
c_data = preparatorCore.dbInterface.get_cache("globus_secret")
client_id = None
refresh_token = None
if (c_data is not None) and c_data.data["StatusCode"] == 0:
    client_id = c_data.data["publicKey"]  # client_id
    refresh_token = c_data.data["privateKey"]  # refresh_token
else:
    client_id = None
    refresh_token = None
    tc = None
    errStr = "failed to get Globus Client ID and Refresh Token"
    tmpLog.error(errStr)
    sys.exit(1)

# create Globus transfer client to send initial files to remote Globus source
tmpStat, tc = globus_utils.create_globus_transfer_client(tmpLog, client_id, refresh_token)
if not tmpStat:
    tc = None
    errStr = "failed to create Globus Transfer Client"
    tmpLog.error(errStr)
    sys.exit(1)
try:
    # We are sending test files from our destination machine to the source machine
    # Test endpoints for activation -
    tmpStatsrc, srcStr = globus_utils.check_endpoint_activation(tmpLog, tc, dstEndpoint)
    tmpStatdst, dstStr = globus_utils.check_endpoint_activation(tmpLog, tc, srcEndpoint)
    if tmpStatsrc and tmpStatdst:
        errStr = "source Endpoint and destination Endpoint activated"
        tmpLog.debug(errStr)
    else:
        errStr = ""
        if not tmpStatsrc:
            errStr += " source Endpoint not activated "
        if not tmpStatdst:
            errStr += " destination Endpoint not activated "
        tmpLog.error(errStr)
        sys.exit(2)
    # We are sending test files from our destination machine to the source machine
    # both endpoints activated now prepare to transfer data
    tdata = TransferData(tc, dstEndpoint, srcEndpoint, sync_level="checksum")
except BaseException:
    errStat, errMsg = globus_utils.handle_globus_exception(tmpLog)
    sys.exit(1)

# create JobSpec
jobSpec = JobSpec()
jobSpec.jobParams = {
    "scopeLog": "panda",
    "logFile": "log",
}
jobSpec.computingSite = queueName
jobSpec.PandaID = job_id
jobSpec.modificationTime = datetime.datetime.now()
realDataset = "panda.sgotest." + uuid.uuid4().hex
ddmEndPointIn = "BNL-OSG2_DATADISK"
inFiles_scope_str = ""
inFiles_str = ""
realDatasets_str = ""
realDatasetsIn_str = ""
ddmEndPointIn_str = ""
GUID_str = ""
fsize_str = ""
checksum_str = ""
scope_in_str = ""

# create up 5 files for input
for index in range(random.randint(1, 5)):
    fileSpec = FileSpec()
    assFileSpec = FileSpec()
    fileSpec.fileType = "input"
    assFileSpec.lfn = "panda.sgotest." + uuid.uuid4().hex
    fileSpec.lfn = assFileSpec.lfn
    fileSpec.scope = "panda"
    inFiles_scope_str += "panda,"
    inFiles_str += fileSpec.lfn + ","
    realDatasets_str += realDataset + ","
    realDatasetsIn_str += realDataset + ","
    ddmEndPointIn_str += ddmEndPointIn + ","
    # some dummy inputs
    GUID_str += "d82e8e5e301b77489fd4da04bcdd6565,"
    fsize_str += "3084569129,"
    checksum_str += "ad:9f60d29f,"
    scope_in_str += "panda,"
    #
    assFileSpec.fileType = "input"
    assFileSpec.fsize = random.randint(10, 100)
    # create source file
    hash = hashlib.md5()
    hash.update(f"{fileSpec.scope}:{fileSpec.lfn}".encode("utf-8"))
    hash_hex = hash.hexdigest()
    correctedscope = "/".join(scope.split("."))
    fileSpec.path = f"{queueConfig.preparator['Globus_dstPath']}/{correctedscope}/{hash_hex[0:2]}/{hash_hex[2:4]}/{fileSpec.lfn}"
    assFileSpec.path = fileSpec.path
    fileSpec.add_associated_file(assFileSpec)
    # now create the temporary file
    tmpfile_path = f"{queueConfig.preparator['basePath']}/testdata/{assFileSpec.lfn}"
    if not os.path.exists(os.path.dirname(tmpfile_path)):
        tmpLog.debug(f"os.makedirs({os.path.dirname(tmpfile_path)})")
        os.makedirs(os.path.dirname(tmpfile_path))
    oFile = open(tmpfile_path, "w")
    oFile.write("".join(random.choice(string.ascii_uppercase + string.digits) for _ in range(assFileSpec.fsize)))
    oFile.close()
    # location of destination file
    destfile_path = f"{queueConfig.preparator['Globus_srcPath']}/{correctedscope}/{hash_hex[0:2]}/{hash_hex[2:4]}/{fileSpec.lfn}"

    # add to Globus transfer list
    tdata.add_item(tmpfile_path, destfile_path)
    # print "dump(fileSpec)"
    # dump(fileSpec)
    # add input file to jobSpec
    jobSpec.add_in_file(fileSpec)
    #
    tmpLog.debug(f"source file to transfer - {tmpfile_path}")
    tmpLog.debug(f"destination file to transfer - {destfile_path}")
    # print "dump(jobSpec)"
    # dump(jobSpec)
# remove final ","
realDatasetsIn_str = realDatasetsIn_str[:-1]
inFiles_str = inFiles_str[:-1]
inFiles_scope_str = inFiles_scope_str[:-1]
GUID_str = GUID_str[:-1]
fsize_str = fsize_str[:-1]
checksum_str = checksum_str[:-1]
scope_in_str = scope_in_str[:-1]
jobSpec.jobParams["realDatasets"] = realDatasets_str
jobSpec.jobParams["ddmEndPointIn"] = ddmEndPointIn_str
jobSpec.jobParams["inFiles"] = inFiles_str
jobSpec.jobParams["GUID"] = GUID_str
jobSpec.jobParams["fsize"] = fsize_str
jobSpec.jobParams["checksum"] = checksum_str
jobSpec.jobParams["scopeIn"] = scope_in_str
jobSpec.jobParams["realDatasetsIn"] = realDatasetsIn_str
msgStr = f"jobSpec.jobParams ={jobSpec.jobParams}"
tmpLog.debug(msgStr)

# transfer dummy files to Remote site for input
transfer_result = tc.submit_transfer(tdata)
# check status code and message
tmpLog.debug(str(transfer_result))
if transfer_result["code"] == "Accepted":
    # succeeded
    # set transfer ID which are used for later lookup
    transferID = transfer_result["task_id"]
    tmpLog.debug("done")
else:
    tmpLog.error("Failed to send intial files")
    sys.exit(3)

print(f"sleep {globus_sleep_time} seconds")
time.sleep(globus_sleep_time)

# enter polling loop to see if the intial files have transfered
maxloop = 5
iloop = 0
NotFound = True
while (iloop < maxloop) and NotFound:
    # get transfer task
    tmpStat, transferTasks = globus_utils.get_transfer_task_by_id(tmpLog, tc, transferID)
    # return a temporary error when failed to get task
    if not tmpStat:
        errStr = "failed to get transfer task"
        tmpLog.error(errStr)
    else:
        # return a temporary error when task is missing
        tmpLog.debug(f"transferTasks : {transferTasks} ")
        if transferID not in transferTasks:
            errStr = f"transfer task ID - {transferID} is missing"
            tmpLog.error(errStr)
        else:
            # succeeded in finding a transfer task by tranferID
            if transferTasks[transferID]["status"] == "SUCCEEDED":
                tmpLog.debug(f"transfer task {transferID} succeeded")
                NotFound = False
            # failed
            if transferTasks[transferID]["status"] == "FAILED":
                errStr = f"transfer task {transferID} failed"
                tmpLog.error(errStr)
        # another status
        tmpStr = f"transfer task {transferID} status: {transferTasks[transferID]['status']}"
        tmpLog.debug(tmpStr)
    if NotFound:
        print(f"sleep {globus_sleep_time} seconds")
        time.sleep(globus_sleep_time)
        ++iloop

if NotFound:
    errStr = f"transfer task ID - {transferID} is missing"
    tmpLog.error(errStr)
    sys.exit(1)

# dump(queueConfig)

print(f"plugin={preparatorCore.__class__.__name__}")

print("testing stagein:")
print(f"BasePath from preparator configuration: {preparatorCore.basePath} ")


tmpStat, tmpOut = preparatorCore.trigger_preparation(jobSpec)
if tmpStat:
    print(" OK")
else:
    print(f" NG {tmpOut}")

print(f"sleep {globus_sleep_time} seconds")
time.sleep(globus_sleep_time)

print("testing status check")
while True:
    tmpStat, tmpOut = preparatorCore.check_stage_in_status(jobSpec)
    if tmpStat:
        print(" OK")
        break
    elif tmpStat == False:
        print(f" NG {tmpOut}")
        sys.exit(1)
    else:
        print(" still running. sleep 1 min")
        time.sleep(60)

print("checking path resolution")
tmpStat, tmpOut = preparatorCore.resolve_input_paths(jobSpec)
if tmpStat:
    print(f" OK {jobSpec.jobParams['inFilePaths']}")
else:
    print(f" NG {tmpOut}")
