from future.utils import iteritems
from pandaharvester.harvesterbody.agent_base import AgentBase
from pandaharvester.harvesterconfig import harvester_config
from pandaharvester.harvestercore import core_utils
from pandaharvester.harvestercore.db_proxy_pool import DBProxyPool as DBProxy
from pandaharvester.harvestercore.plugin_factory import PluginFactory
from pandaharvester.harvestercore.work_spec import WorkSpec

# logger
_logger = core_utils.setup_logger("event_feeder")


# class to feed events to workers
class EventFeeder(AgentBase):
    # constructor
    def __init__(self, communicator, queue_config_mapper, single_mode=False):
        AgentBase.__init__(self, single_mode)
        self.dbProxy = DBProxy()
        self.queueConfigMapper = queue_config_mapper
        self.communicator = communicator
        self.pluginFactory = PluginFactory()

    # main loop
    def run(self):
        lockedBy = f"eventfeeder-{self.get_pid()}"
        while True:
            mainLog = self.make_logger(_logger, f"id={lockedBy}", method_name="run")
            mainLog.debug("getting workers to feed events")
            workSpecsPerQueue = self.dbProxy.get_workers_to_feed_events(
                harvester_config.eventfeeder.maxWorkers, harvester_config.eventfeeder.lockInterval, lockedBy
            )
            mainLog.debug(f"got {len(workSpecsPerQueue)} queues")
            # loop over all workers
            for queueName, workSpecList in iteritems(workSpecsPerQueue):
                tmpQueLog = self.make_logger(_logger, f"queue={queueName}", method_name="run")
                # check queue
                if not self.queueConfigMapper.has_queue(queueName):
                    tmpQueLog.error("config not found")
                    continue
                # get queue
                queueConfig = self.queueConfigMapper.get_queue(queueName)
                if hasattr(queueConfig, "scatteredEvents") and queueConfig.scatteredEvents:
                    scattered = True
                else:
                    scattered = False
                # get plugin
                messenger = self.pluginFactory.get_plugin(queueConfig.messenger)
                # loop over all workers
                for workSpec in workSpecList:
                    tmpLog = core_utils.make_logger(_logger, f"workerID={workSpec.workerID}", method_name="run")
                    # lock worker again
                    lockedFlag = self.dbProxy.lock_worker_again_to_feed_events(workSpec.workerID, lockedBy)
                    if not lockedFlag:
                        tmpLog.debug("skipped since locked by another")
                        continue
                    # get events
                    tmpLog.debug("get events")
                    tmpStat, events = self.communicator.get_event_ranges(workSpec.eventsRequestParams, scattered, workSpec.get_access_point())
                    # failed
                    if tmpStat is False:
                        tmpLog.error(f"failed to get events with {events}")
                        continue
                    # lock worker again
                    lockedFlag = self.dbProxy.lock_worker_again_to_feed_events(workSpec.workerID, lockedBy)
                    if not lockedFlag:
                        tmpLog.debug("skipped before feeding since locked by another")
                        continue
                    tmpStat = messenger.feed_events(workSpec, events)
                    # failed
                    if tmpStat is False:
                        tmpLog.error("failed to feed events")
                        continue
                    # dump
                    for pandaID, eventList in iteritems(events):
                        try:
                            nRanges = workSpec.eventsRequestParams[pandaID]["nRanges"]
                        except Exception:
                            nRanges = None
                        tmpLog.debug(f"got {len(eventList)} events for PandaID={pandaID} while getting {nRanges} events")
                        # disable multi workers
                        if workSpec.mapType == WorkSpec.MT_MultiWorkers:
                            if len(eventList) == 0 or (nRanges is not None and len(eventList) < nRanges):
                                tmpStat = self.dbProxy.disable_multi_workers(pandaID)
                                if tmpStat == 1:
                                    tmpStr = f"disabled MultiWorkers for PandaID={pandaID}"
                                    tmpLog.debug(tmpStr)
                    # update worker
                    workSpec.eventsRequest = WorkSpec.EV_useEvents
                    workSpec.eventsRequestParams = None
                    workSpec.eventFeedTime = None
                    workSpec.eventFeedLock = None
                    # update local database
                    tmpStat = self.dbProxy.update_worker(workSpec, {"eventFeedLock": lockedBy})
                    tmpLog.debug(f"done with {tmpStat}")
                tmpQueLog.debug("done")
            mainLog.debug("done")
            # check if being terminated
            if self.terminated(harvester_config.eventfeeder.sleepTime):
                mainLog.debug("terminated")
                return
