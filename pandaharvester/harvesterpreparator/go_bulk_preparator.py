import datetime
import hashlib
import os
import os.path
import string
import sys
import threading
import time
import uuid
import zipfile

# TO BE REMOVED for python2.7
import requests.packages.urllib3
from future.utils import iteritems
from globus_sdk import (
    NativeAppAuthClient,
    RefreshTokenAuthorizer,
    TransferClient,
    TransferData,
)
from pandaharvester.harvestermisc import globus_utils

try:
    requests.packages.urllib3.disable_warnings()
except BaseException:
    pass
from pandaharvester.harvesterconfig import harvester_config
from pandaharvester.harvestercore import core_utils
from pandaharvester.harvestercore.plugin_base import PluginBase
from pandaharvester.harvestercore.queue_config_mapper import QueueConfigMapper
from pandaharvester.harvestermover import mover_utils

# Define dummy transfer identifier
dummy_transfer_id_base = "dummy_id_for_in"
# lock to get a unique ID
uLock = threading.Lock()

# number to get a unique ID
uID = 0

# logger
_logger = core_utils.setup_logger("go_bulk_preparator")


def validate_transferid(transferid):
    tmptransferid = transferid.replace("-", "")
    return all(c in string.hexdigits for c in tmptransferid)


def dump(obj):
    for attr in dir(obj):
        if hasattr(obj, attr):
            print(f"obj.{attr} = {getattr(obj, attr)}")


# Globus plugin for stager with bulk transfers. For JobSpec and DBInterface methods, see
# https://github.com/PanDAWMS/panda-harvester/wiki/Utilities#file-grouping-for-file-transfers
class GlobusBulkPreparator(PluginBase):
    next_id = 0
    # constructor

    def __init__(self, **kwarg):
        PluginBase.__init__(self, **kwarg)
        # make logger
        tmpLog = self.make_logger(_logger, f"ThreadID={threading.current_thread().ident}", method_name="GlobusBulkPreparator __init__ {} ")
        tmpLog.debug("__init__ start")
        self.thread_id = threading.current_thread().ident
        self.id = GlobusBulkPreparator.next_id
        GlobusBulkPreparator.next_id += 1
        with uLock:
            global uID
            self.dummy_transfer_id = f"{dummy_transfer_id_base}_XXXX"
            uID += 1
            uID %= harvester_config.preparator.nThreads
        # create Globus Transfer Client
        try:
            self.tc = None
            # need to get client_id and refresh_token from PanDA server via harvester cache mechanism
            tmpLog.debug("about to call dbInterface.get_cache(globus_secret)")
            c_data = self.dbInterface.get_cache("globus_secret")
            if (c_data is not None) and c_data.data["StatusCode"] == 0:
                tmpLog.debug("Got the globus_secrets from PanDA")
                self.client_id = c_data.data["publicKey"]  # client_id
                self.refresh_token = c_data.data["privateKey"]  # refresh_token
                tmpStat, self.tc = globus_utils.create_globus_transfer_client(tmpLog, self.client_id, self.refresh_token)
                if not tmpStat:
                    self.tc = None
                    errStr = "failed to create Globus Transfer Client"
                    tmpLog.error(errStr)
            else:
                self.client_id = None
                self.refresh_token = None
                self.tc = None
                errStr = "failed to get Globus Client ID and Refresh Token"
                tmpLog.error(errStr)
        except BaseException:
            core_utils.dump_error_message(tmpLog)
        # tmp debugging
        tmpLog.debug(f"self.id = {self.id}")
        tmpLog.debug(f"self.dummy_transfer_id = {self.dummy_transfer_id}")
        # tmp debugging
        tmpLog.debug("__init__ finish")

    # get dummy_transfer_id

    def get_dummy_transfer_id(self):
        return self.dummy_transfer_id

    # set dummy_transfer_id for testing
    def set_dummy_transfer_id_testing(self, dummy_transfer_id):
        self.dummy_transfer_id = dummy_transfer_id

    # set FileSpec.status
    def set_FileSpec_status(self, jobspec, status):
        # loop over all input files
        for fileSpec in jobspec.inFiles:
            fileSpec.status = status

    # check status
    def check_stage_in_status(self, jobspec):
        # make logger
        tmpLog = self.make_logger(_logger, f"PandaID={jobspec.PandaID} ThreadID={threading.current_thread().ident}", method_name="check_stage_in_status")
        tmpLog.debug("start")
        # check that jobspec.computingSite is defined
        if jobspec.computingSite is None:
            # not found
            tmpLog.error("jobspec.computingSite is not defined")
            return False, "jobspec.computingSite is not defined"
        else:
            tmpLog.debug(f"jobspec.computingSite : {jobspec.computingSite}")
        # show the dummy transfer id and set to a value with the jobspec.computingSite if needed.
        tmpLog.debug(f"self.dummy_transfer_id = {self.dummy_transfer_id}")
        if self.dummy_transfer_id == f"{dummy_transfer_id_base}_XXXX":
            old_dummy_transfer_id = self.dummy_transfer_id
            self.dummy_transfer_id = f"{dummy_transfer_id_base}_{jobspec.computingSite}"
            tmpLog.debug(f"Change self.dummy_transfer_id  from {old_dummy_transfer_id} to {self.dummy_transfer_id}")

        # default return
        tmpRetVal = (True, "")
        # set flag if have db lock
        have_db_lock = False
        queueConfigMapper = QueueConfigMapper()
        queueConfig = queueConfigMapper.get_queue(jobspec.computingSite)
        # test we have a Globus Transfer Client
        if not self.tc:
            errStr = "failed to get Globus Transfer Client"
            tmpLog.error(errStr)
            return False, errStr
        # set transferID to None
        transferID = None
        # get transfer groups
        groups = jobspec.get_groups_of_input_files(skip_ready=True)
        tmpLog.debug(f"jobspec.get_groups_of_input_files() = : {groups}")
        # lock if the dummy transfer ID is used to avoid submitting duplicated transfer requests
        for dummy_transferID in groups:
            # skip if valid transfer ID not dummy one
            if validate_transferid(dummy_transferID):
                continue
            # lock for 120 sec
            tmpLog.debug(
                f"attempt to set DB lock for self.id - {self.id} self.dummy_transfer_id - {self.dummy_transfer_id}, dummy_transferID - {dummy_transferID}"
            )
            have_db_lock = self.dbInterface.get_object_lock(dummy_transferID, lock_interval=120)
            tmpLog.debug(f" DB lock result - {have_db_lock}")
            if not have_db_lock:
                # escape since locked by another thread
                msgStr = "escape since locked by another thread"
                tmpLog.debug(msgStr)
                return None, msgStr
            # refresh group information since that could have been updated by another thread before getting the lock
            tmpLog.debug("self.dbInterface.refresh_file_group_info(jobspec)")
            self.dbInterface.refresh_file_group_info(jobspec)
            tmpLog.debug("after self.dbInterface.refresh_file_group_info(jobspec)")
            # get transfer groups again with refreshed info
            tmpLog.debug("groups = jobspec.get_groups_of_input_files(skip_ready=True)")
            groups = jobspec.get_groups_of_input_files(skip_ready=True)
            tmpLog.debug(f"after db lock and refresh - jobspec.get_groups_of_input_files(skip_ready=True) = : {groups}")
            # the dummy transfer ID is still there
            if dummy_transferID in groups:
                groupUpdateTime = groups[dummy_transferID]["groupUpdateTime"]
                # get files with the dummy transfer ID across jobs
                fileSpecs_allgroups = self.dbInterface.get_files_with_group_id(dummy_transferID)
                msgStr = "dummy_transferID = {0} self.dbInterface.get_files_with_group_id(dummy_transferID)  number of files = {1}".format(
                    dummy_transferID, len(fileSpecs_allgroups)
                )
                tmpLog.debug(msgStr)
                fileSpecs = jobspec.get_input_file_specs(dummy_transferID, skip_ready=True)
                msgStr = "dummy_transferID = {0} jobspec.get_input_file_specs(dummy_transferID,skip_ready=True)  number of files = {1}".format(
                    dummy_transferID, len(fileSpecs)
                )
                tmpLog.debug(msgStr)
                # submit transfer if there are more than 10 files or the group was made before more than 10 min
                if len(fileSpecs) >= 10 or groupUpdateTime < datetime.datetime.utcnow() - datetime.timedelta(minutes=10):
                    tmpLog.debug("prepare to transfer files")
                    # submit transfer and get a real transfer ID
                    # set the Globus destination Endpoint id and path will get them from Agis eventually
                    self.Globus_srcPath = queueConfig.preparator["Globus_srcPath"]
                    self.srcEndpoint = queueConfig.preparator["srcEndpoint"]
                    self.Globus_dstPath = self.basePath
                    # self.Globus_dstPath = queueConfig.preparator['Globus_dstPath']
                    self.dstEndpoint = queueConfig.preparator["dstEndpoint"]
                    # Test the endpoints and create the transfer data class
                    errMsg = None
                    try:
                        # Test endpoints for activation
                        tmpStatsrc, srcStr = globus_utils.check_endpoint_activation(tmpLog, self.tc, self.srcEndpoint)
                        tmpStatdst, dstStr = globus_utils.check_endpoint_activation(tmpLog, self.tc, self.dstEndpoint)
                        if tmpStatsrc and tmpStatdst:
                            errStr = "source Endpoint and destination Endpoint activated"
                            tmpLog.debug(errStr)
                        else:
                            errMsg = ""
                            if not tmpStatsrc:
                                errMsg += " source Endpoint not activated "
                            if not tmpStatdst:
                                errMsg += " destination Endpoint not activated "
                            # release process lock
                            tmpLog.debug(
                                "attempt to release DB lock for self.id - {0} self.dummy_transfer_id - {1}, dummy_transferID - {2}".format(
                                    self.id, self.dummy_transfer_id, dummy_transferID
                                )
                            )
                            have_db_lock = self.dbInterface.release_object_lock(dummy_transferID)
                            if not have_db_lock:
                                errMsg += f" - Could not release DB lock for {dummy_transferID}"
                            tmpLog.error(errMsg)
                            tmpRetVal = (None, errMsg)
                            return tmpRetVal
                        # both endpoints activated now prepare to transfer data
                        tdata = None
                        tdata = TransferData(self.tc, self.srcEndpoint, self.dstEndpoint, sync_level="exists")
                        #                                             sync_level="checksum")
                        tmpLog.debug(f"size of tdata[DATA] - {len(tdata['DATA'])}")

                    except BaseException:
                        errStat, errMsg = globus_utils.handle_globus_exception(tmpLog)
                        # release process lock
                        tmpLog.debug(
                            "attempt to release DB lock for self.id - {0} self.dummy_transfer_id - {1}, dummy_transferID - {2}".format(
                                self.id, self.dummy_transfer_id, dummy_transferID
                            )
                        )
                        release_db_lock = self.dbInterface.release_object_lock(dummy_transferID)
                        if not release_db_lock:
                            errMsg += f" - Could not release DB lock for {self.dummy_transferID}"
                        tmpLog.error(errMsg)
                        tmpRetVal = (errStat, errMsg)
                        return tmpRetVal
                    # loop over all files
                    ifile = 0
                    for fileSpec in fileSpecs:
                        # only print to log file first 25 files
                        if ifile < 25:
                            msgStr = f"fileSpec.lfn - {fileSpec.lfn} fileSpec.scope - {fileSpec.scope}"
                            tmpLog.debug(msgStr)
                        if ifile == 25:
                            msgStr = "printed first 25 files skipping the rest".format(fileSpec.lfn, fileSpec.scope)
                            tmpLog.debug(msgStr)
                        # end debug log file test
                        scope = "panda"
                        if fileSpec.scope is not None:
                            scope = fileSpec.scope
                        hash = hashlib.md5()
                        if sys.version_info.major == 2:
                            hash.update(f"{scope}:{fileSpec.lfn}")
                        if sys.version_info.major == 3:
                            hash_string = f"{scope}:{fileSpec.lfn}"
                            hash.update(bytes(hash_string, "utf-8"))
                        hash_hex = hash.hexdigest()
                        correctedscope = "/".join(scope.split("."))
                        # srcURL = fileSpec.path
                        srcURL = f"{self.Globus_srcPath}/{correctedscope}/{hash_hex[0:2]}/{hash_hex[2:4]}/{fileSpec.lfn}"
                        dstURL = f"{self.Globus_dstPath}/{correctedscope}/{hash_hex[0:2]}/{hash_hex[2:4]}/{fileSpec.lfn}"
                        # add files to transfer object - tdata
                        if ifile < 25:
                            tmpLog.debug(f"tdata.add_item({srcURL},{dstURL})")
                        tdata.add_item(srcURL, dstURL)
                        ifile += 1
                    # submit transfer
                    tmpLog.debug(f"Number of files to transfer - {len(tdata['DATA'])}")
                    try:
                        transfer_result = self.tc.submit_transfer(tdata)
                        # check status code and message
                        tmpLog.debug(str(transfer_result))
                        if transfer_result["code"] == "Accepted":
                            # succeeded
                            # set transfer ID which are used for later lookup
                            transferID = transfer_result["task_id"]
                            tmpLog.debug(f"successfully submitted id={transferID}")
                            # set status for files
                            self.dbInterface.set_file_group(fileSpecs, transferID, "running")
                            msgStr = f"submitted transfer with ID={transferID}"
                            tmpLog.debug(msgStr)
                        else:
                            # release process lock
                            tmpLog.debug(f"attempt to release DB lock for self.id - {self.id} dummy_transferID - {dummy_transferID}")
                            release_db_lock = self.dbInterface.release_object_lock(dummy_transferID)
                            if release_db_lock:
                                tmpLog.debug(f"Released DB lock for self.id - {self.id} dummy_transferID - {dummy_transferID}")
                                have_db_lock = False
                            else:
                                errMsg = f"Could not release DB lock for {dummy_transferID}"
                                tmpLog.error(errMsg)
                            tmpRetVal = (None, transfer_result["message"])
                            return tmpRetVal
                    except Exception as e:
                        errStat, errMsg = globus_utils.handle_globus_exception(tmpLog)
                        # release process lock
                        tmpLog.debug(f"attempt to release DB lock for self.id - {self.id} dummy_transferID - {dummy_transferID}")
                        release_db_lock = self.dbInterface.release_object_lock(dummy_transferID)
                        if release_db_lock:
                            tmpLog.debug(f"Released DB lock for self.id - {self.id} dummy_transferID - {dummy_transferID}")
                            have_db_lock = False
                        else:
                            errMsg += f" - Could not release DB lock for {dummy_transferID}"
                        tmpLog.error(errMsg)
                        return errStat, errMsg
                else:
                    msgStr = "wait until enough files are pooled"
                    tmpLog.debug(msgStr)
                # release the lock
                tmpLog.debug(f"attempt to release DB lock for self.id - {self.id} dummy_transferID - {dummy_transferID}")
                release_db_lock = self.dbInterface.release_object_lock(dummy_transferID)
                if release_db_lock:
                    tmpLog.debug(f"released DB lock for self.id - {self.id} dummy_transferID - {dummy_transferID}")
                    have_db_lock = False
                else:
                    msgStr += f" - Could not release DB lock for {dummy_transferID}"
                    tmpLog.error(msgStr)
                # return None to retry later
                return None, msgStr
            # release the db lock if needed
            if have_db_lock:
                tmpLog.debug(f"attempt to release DB lock for self.id - {self.id} dummy_transferID - {dummy_transferID}")
                release_db_lock = self.dbInterface.release_object_lock(dummy_transferID)
                if release_db_lock:
                    tmpLog.debug(f"released DB lock for self.id - {self.id} dummy_transferID - {dummy_transferID}")
                    have_db_lock = False
                else:
                    msgStr += f" - Could not release DB lock for {dummy_transferID}"
                    tmpLog.error(msgStr)
                    return None, msgStr
        # check transfer with real transfer IDs
        # get transfer groups
        tmpLog.debug("groups = jobspec.get_groups_of_input_files(skip_ready=True)")
        groups = jobspec.get_groups_of_input_files(skip_ready=True)
        tmpLog.debug(f"Number of transfer groups (skip_ready)- {len(groups)}")
        tmpLog.debug(f"transfer groups any state (skip_ready)- {groups}")
        tmpLog.debug("groups = jobspec.get_groups_of_input_files()")
        groups = jobspec.get_groups_of_input_files()
        tmpLog.debug(f"Number of transfer groups - {len(groups)}")
        tmpLog.debug(f"transfer groups any state - {groups}")
        tmpLog.debug("groups = jobspec.get_groups_of_input_files(skip_ready=True)")
        groups = jobspec.get_groups_of_input_files(skip_ready=True)
        if len(groups) == 0:
            tmpLog.debug("jobspec.get_groups_of_input_files(skip_ready=True) returned no files ")
            tmpLog.debug("check_stage_in_status return status - True ")
            return True, ""
        for transferID in groups:
            # allow only valid UUID
            if validate_transferid(transferID):
                # get transfer task
                tmpStat, transferTasks = globus_utils.get_transfer_task_by_id(tmpLog, self.tc, transferID)
                # return a temporary error when failed to get task
                if not tmpStat:
                    errStr = f"failed to get transfer task; tc = {str(self.tc)}; transferID = {str(transferID)}"
                    tmpLog.error(errStr)
                    return None, errStr
                # return a temporary error when task is missing
                if transferID not in transferTasks:
                    errStr = f"transfer task ID - {transferID} is missing"
                    tmpLog.error(errStr)
                    return None, errStr
                # succeeded in finding a transfer task by tranferID
                if transferTasks[transferID]["status"] == "SUCCEEDED":
                    tmpLog.debug(f"transfer task {transferID} succeeded")
                    self.set_FileSpec_status(jobspec, "finished")
                    return True, ""
                # failed
                if transferTasks[transferID]["status"] == "FAILED":
                    errStr = f"transfer task {transferID} failed"
                    tmpLog.error(errStr)
                    self.set_FileSpec_status(jobspec, "failed")
                    return False, errStr
                # another status
                tmpStr = f"transfer task {transferID} status: {transferTasks[transferID]['status']}"
                tmpLog.debug(tmpStr)
                return None, tmpStr
        # end of loop over transfer groups
        tmpLog.debug("End of loop over transfers groups - ending check_stage_in_status function")
        return None, "no valid transfer id found"

    # trigger preparation

    def trigger_preparation(self, jobspec):
        # make logger
        tmpLog = self.make_logger(_logger, f"PandaID={jobspec.PandaID} ThreadID={threading.current_thread().ident}", method_name="trigger_preparation")
        tmpLog.debug("start")
        # default return
        tmpRetVal = (True, "")
        # check that jobspec.computingSite is defined
        if jobspec.computingSite is None:
            # not found
            tmpLog.error("jobspec.computingSite is not defined")
            return False, "jobspec.computingSite is not defined"
        else:
            tmpLog.debug(f"jobspec.computingSite : {jobspec.computingSite}")
        # test we have a Globus Transfer Client
        if not self.tc:
            errStr = "failed to get Globus Transfer Client"
            tmpLog.error(errStr)
            return False, errStr
        # show the dummy transfer id and set to a value with the computingSite if needed.
        tmpLog.debug(f"self.dummy_transfer_id = {self.dummy_transfer_id}")
        if self.dummy_transfer_id == f"{dummy_transfer_id_base}_XXXX":
            old_dummy_transfer_id = self.dummy_transfer_id
            self.dummy_transfer_id = f"{dummy_transfer_id_base}_{jobspec.computingSite}"
            tmpLog.debug(f"Change self.dummy_transfer_id  from {old_dummy_transfer_id} to {self.dummy_transfer_id}")
        # set the dummy transfer ID which will be replaced with a real ID in check_stage_in_status()
        inFiles = jobspec.get_input_file_attributes(skip_ready=True)
        lfns = list(inFiles.keys())
        # for inLFN in inFiles.keys():
        #    lfns.append(inLFN)
        tmpLog.debug(f"number of lfns - {len(lfns)} type(lfns) - {type(lfns)}")
        jobspec.set_groups_to_files({self.dummy_transfer_id: {"lfns": lfns, "groupStatus": "pending"}})
        if len(lfns) < 10:
            msgStr = f"jobspec.set_groups_to_files - self.dummy_tranfer_id - {self.dummy_transfer_id}, lfns - {lfns}, groupStatus - pending"
        else:
            tmp_lfns = lfns[:10]
            msgStr = f"jobspec.set_groups_to_files - self.dummy_tranfer_id - {self.dummy_transfer_id}, lfns (first 25) - {tmp_lfns}, groupStatus - pending"
        tmpLog.debug(msgStr)
        fileSpec_list = jobspec.get_input_file_specs(self.dummy_transfer_id, skip_ready=True)
        tmpLog.debug(f"call jobspec.get_input_file_specs({self.dummy_transfer_id}, skip_ready=True) num files returned = {len(fileSpec_list)}")
        tmpLog.debug(
            "call self.dbInterface.set_file_group(jobspec.get_input_file_specs(self.dummy_transfer_id,skip_ready=True),self.dummy_transfer_id,pending)"
        )
        tmpStat = self.dbInterface.set_file_group(fileSpec_list, self.dummy_transfer_id, "pending")
        msgStr = "called self.dbInterface.set_file_group(jobspec.get_input_file_specs(self.dummy_transfer_id,skip_ready=True),self.dummy_transfer_id,pending) return Status {}".format(
            tmpStat
        )
        tmpLog.debug(msgStr)
        return True, ""

    # make label for transfer task

    def make_label(self, jobspec):
        return f"IN-{jobspec.computingSite}-{jobspec.PandaID}"

    # resolve input file paths
    def resolve_input_paths(self, jobspec):
        # get input files
        inFiles = jobspec.get_input_file_attributes()
        # set path to each file
        for inLFN, inFile in iteritems(inFiles):
            inFile["path"] = mover_utils.construct_file_path(self.basePath, inFile["scope"], inLFN)
        # set
        jobspec.set_input_file_paths(inFiles)
        return True, ""

    # Globus specific commands
