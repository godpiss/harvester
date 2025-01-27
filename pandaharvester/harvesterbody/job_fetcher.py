import datetime
import random
import socket

from future.utils import iteritems
from pandaharvester.harvesterbody.agent_base import AgentBase
from pandaharvester.harvesterconfig import harvester_config
from pandaharvester.harvestercore import core_utils
from pandaharvester.harvestercore.db_proxy_pool import DBProxyPool as DBProxy
from pandaharvester.harvestercore.file_spec import FileSpec
from pandaharvester.harvestercore.job_spec import JobSpec
from pandaharvester.harvestercore.plugin_factory import PluginFactory
from pandaharvester.harvestermisc.info_utils import PandaQueuesDict

# logger
_logger = core_utils.setup_logger("job_fetcher")


# class to fetch jobs
class JobFetcher(AgentBase):
    # constructor
    def __init__(self, communicator, queue_config_mapper, single_mode=False):
        AgentBase.__init__(self, single_mode)
        self.dbProxy = DBProxy()
        self.communicator = communicator
        self.nodeName = socket.gethostname()
        self.queueConfigMapper = queue_config_mapper
        self.pluginFactory = PluginFactory()

    # main loop
    def run(self):
        while True:
            mainLog = self.make_logger(_logger, f"id={self.get_pid()}", method_name="run")
            mainLog.debug("getting number of jobs to be fetched")
            # get number of jobs to be fetched
            nJobsPerQueue = self.dbProxy.get_num_jobs_to_fetch(harvester_config.jobfetcher.nQueues, harvester_config.jobfetcher.lookupTime)
            mainLog.debug(f"got {len(nJobsPerQueue)} queues")

            # get up to date queue configuration
            pandaQueueDict = PandaQueuesDict()

            # loop over all queues
            for queueName, nJobs in iteritems(nJobsPerQueue):
                # check queue
                if not self.queueConfigMapper.has_queue(queueName):
                    continue
                tmpLog = self.make_logger(_logger, f"queueName={queueName}", method_name="run")
                # get queue
                queueConfig = self.queueConfigMapper.get_queue(queueName)
                siteName = queueConfig.siteName
                # upper limit
                if nJobs > harvester_config.jobfetcher.maxJobs:
                    nJobs = harvester_config.jobfetcher.maxJobs

                # get jobs
                try:
                    is_grandly_unified_queue = pandaQueueDict.is_grandly_unified_queue(siteName)
                except Exception:
                    is_grandly_unified_queue = False

                default_prodSourceLabel = queueConfig.get_source_label(is_gu=is_grandly_unified_queue)

                pdpm = getattr(queueConfig, "prodSourceLabelRandomWeightsPermille", {})
                choice_list = core_utils.make_choice_list(pdpm=pdpm, default=default_prodSourceLabel)
                prodSourceLabel = random.choice(choice_list)
                tmpLog.debug(f"getting {nJobs} jobs for prodSourceLabel {prodSourceLabel}")
                sw = core_utils.get_stopwatch()
                jobs, errStr = self.communicator.get_jobs(siteName, self.nodeName, prodSourceLabel, self.nodeName, nJobs, queueConfig.getJobCriteria)
                tmpLog.info(f"got {len(jobs)} jobs with {errStr} {sw.get_elapsed_time()}")
                # convert to JobSpec
                if len(jobs) > 0:
                    # get extractor plugin
                    if hasattr(queueConfig, "extractor"):
                        extractorCore = self.pluginFactory.get_plugin(queueConfig.extractor)
                    else:
                        extractorCore = None
                    jobSpecs = []
                    fileStatMap = dict()
                    sw_startconvert = core_utils.get_stopwatch()
                    for job in jobs:
                        timeNow = datetime.datetime.utcnow()
                        jobSpec = JobSpec()
                        jobSpec.convert_job_json(job)
                        jobSpec.computingSite = queueName
                        jobSpec.status = "starting"
                        jobSpec.subStatus = "fetched"
                        jobSpec.creationTime = timeNow
                        jobSpec.stateChangeTime = timeNow
                        jobSpec.configID = queueConfig.configID
                        jobSpec.set_one_attribute("schedulerID", f"harvester-{harvester_config.master.harvester_id}")
                        if queueConfig.zipPerMB is not None and jobSpec.zipPerMB is None:
                            jobSpec.zipPerMB = queueConfig.zipPerMB
                        fileGroupDictList = [jobSpec.get_input_file_attributes()]
                        if extractorCore is not None:
                            fileGroupDictList.append(extractorCore.get_aux_inputs(jobSpec))
                        for fileGroupDict in fileGroupDictList:
                            for tmpLFN, fileAttrs in iteritems(fileGroupDict):
                                # make file spec
                                fileSpec = FileSpec()
                                fileSpec.PandaID = jobSpec.PandaID
                                fileSpec.taskID = jobSpec.taskID
                                fileSpec.lfn = tmpLFN
                                fileSpec.endpoint = queueConfig.ddmEndpointIn
                                fileSpec.scope = fileAttrs["scope"]
                                if "INTERNAL_FileType" in fileAttrs:
                                    fileSpec.fileType = fileAttrs["INTERNAL_FileType"]
                                    jobSpec.auxInput = JobSpec.AUX_hasAuxInput
                                else:
                                    fileSpec.fileType = "input"
                                # check file status
                                if tmpLFN not in fileStatMap:
                                    fileStatMap[tmpLFN] = self.dbProxy.get_file_status(tmpLFN, fileSpec.fileType, queueConfig.ddmEndpointIn, "starting")
                                # set preparing to skip stage-in if the file is (being) taken care of by another job
                                if [x for x in ["ready", "preparing", "to_prepare", "triggered"] if x in fileStatMap[tmpLFN]]:
                                    fileSpec.status = "preparing"
                                else:
                                    fileSpec.status = "to_prepare"
                                fileStatMap[tmpLFN].setdefault(fileSpec.status, None)
                                if "INTERNAL_URL" in fileAttrs:
                                    fileSpec.url = fileAttrs["INTERNAL_URL"]
                                jobSpec.add_in_file(fileSpec)
                        jobSpec.trigger_propagation()
                        jobSpecs.append(jobSpec)
                    # insert to DB
                    tmpLog.debug(f"Converting of {len(jobs)} jobs {sw_startconvert.get_elapsed_time()}")
                    sw_insertdb = core_utils.get_stopwatch()
                    self.dbProxy.insert_jobs(jobSpecs)
                    tmpLog.debug(f"Insert of {len(jobSpecs)} jobs {sw_insertdb.get_elapsed_time()}")
            mainLog.debug("done")
            # check if being terminated
            if self.terminated(harvester_config.jobfetcher.sleepTime):
                mainLog.debug("terminated")
                return
