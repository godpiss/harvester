import collections
import datetime
import json
import os
import socket
import time
from calendar import timegm

from future.utils import iteritems

try:
    import cPickle as pickle
except ImportError:
    import pickle

try:
    from threading import get_ident
except ImportError:
    from thread import get_ident

from pandaharvester.harvesterconfig import harvester_config
from pandaharvester.harvestercore import core_utils
from pandaharvester.harvestercore.db_interface import DBInterface
from pandaharvester.harvestercore.db_proxy_pool import DBProxyPool as DBProxy
from pandaharvester.harvestercore.plugin_factory import PluginFactory

# attribute list
_attribute_list = ["id", "item", "score"]

# fifo object spec
FifoObject = collections.namedtuple("FifoObject", _attribute_list, rename=False)

# logger
_logger = core_utils.setup_logger("fifos")

# base class of fifo message queue


class FIFOBase(object):
    # constructor
    def __init__(self, **kwarg):
        for tmpKey, tmpVal in iteritems(kwarg):
            setattr(self, tmpKey, tmpVal)
        self.hostname = socket.gethostname()
        self.os_pid = os.getpid()
        self.dbProxy = DBProxy()
        self.dbInterface = DBInterface()

    # get process identifier
    def get_pid(self):
        thread_id = get_ident()
        if thread_id is None:
            thread_id = 0
        return f"{self.hostname}_{self.os_pid}-{format(get_ident(), 'x')}"

    # make logger
    def make_logger(self, base_log, token=None, method_name=None, send_dialog=True):
        if send_dialog and hasattr(self, "dbInterface"):
            hook = self.dbInterface
        else:
            hook = None
        return core_utils.make_logger(base_log, token=token, method_name=method_name, hook=hook)

    # intialize fifo from harvester configuration
    def _initialize_fifo(self, force_enable=False):
        self.fifoName = f"{self.titleName}_fifo"
        self.config = getattr(harvester_config, self.titleName)
        if force_enable:
            self.enabled = True
        elif hasattr(self.config, "fifoEnable") and self.config.fifoEnable:
            self.enabled = True
        else:
            self.enabled = False
            return
        pluginConf = vars(self.config).copy()
        pluginConf.update({"titleName": self.titleName})
        if hasattr(self.config, "fifoModule") and hasattr(self.config, "fifoClass"):
            pluginConf.update(
                {
                    "module": self.config.fifoModule,
                    "name": self.config.fifoClass,
                }
            )
        else:
            if not hasattr(harvester_config, "fifo"):
                return
            pluginConf.update(
                {
                    "module": harvester_config.fifo.fifoModule,
                    "name": harvester_config.fifo.fifoClass,
                }
            )
        pluginFactory = PluginFactory()
        self.fifo = pluginFactory.get_plugin(pluginConf)

    # encode
    def encode(self, item):
        item_serialized = pickle.dumps(item, -1)
        return item_serialized

    # decode
    def decode(self, item_serialized):
        item = pickle.loads(item_serialized)
        return item

    # size of queue
    def size(self):
        mainLog = self.make_logger(_logger, f"id={self.fifoName}-{self.get_pid()}", method_name="size")
        retVal = self.fifo.size()
        mainLog.debug(f"size={retVal}")
        return retVal

    # enqueue
    def put(self, item, score=None, encode_item=True):
        mainLog = self.make_logger(_logger, f"id={self.fifoName}-{self.get_pid()}", method_name="put")
        if encode_item:
            item_serialized = self.encode(item)
        else:
            item_serialized = item
        if score is None:
            score = time.time()
        retVal = self.fifo.put(item_serialized, score)
        mainLog.debug(f"score={score}")
        return retVal

    # enqueue by id, which is unique
    def putbyid(self, id, item, score=None, encode_item=True):
        mainLog = self.make_logger(_logger, f"id={self.fifoName}-{self.get_pid()}", method_name="putbyid")
        if encode_item:
            item_serialized = self.encode(item)
        else:
            item_serialized = item
        if score is None:
            score = time.time()
        retVal = self.fifo.putbyid(id, item_serialized, score)
        mainLog.debug(f"id={id} score={score}")
        return retVal

    # dequeue to get the first fifo object
    def get(self, timeout=None, protective=False, decode_item=True):
        mainLog = self.make_logger(_logger, f"id={self.fifoName}-{self.get_pid()}", method_name="get")
        object_tuple = self.fifo.get(timeout, protective)
        if object_tuple is None:
            retVal = None
        else:
            id, item_serialized, score = object_tuple
            if item_serialized is not None and decode_item:
                item = self.decode(item_serialized)
            else:
                item = item_serialized
            retVal = FifoObject(id, item, score)
        mainLog.debug(f"called. protective={protective} decode_item={decode_item}")
        return retVal

    # dequeue to get the last fifo object
    def getlast(self, timeout=None, protective=False, decode_item=True):
        mainLog = self.make_logger(_logger, f"id={self.fifoName}-{self.get_pid()}", method_name="getlast")
        object_tuple = self.fifo.getlast(timeout, protective)
        if object_tuple is None:
            retVal = None
        else:
            id, item_serialized, score = object_tuple
            if item_serialized is not None and decode_item:
                item = self.decode(item_serialized)
            else:
                item = item_serialized
            retVal = FifoObject(id, item, score)
        mainLog.debug(f"called. protective={protective} decode_item={decode_item}")
        return retVal

    # dequeue list of objects with some conditions
    def getmany(self, mode="first", minscore=None, maxscore=None, count=None, protective=False, temporary=False, decode_item=True):
        mainLog = self.make_logger(_logger, f"id={self.fifoName}-{self.get_pid()}", method_name="getmany")
        object_tuple_list = self.fifo.getmany(mode, minscore, maxscore, count, protective, temporary)
        if not object_tuple_list:
            mainLog.debug("empty list")
        ret_list = []
        for object_tuple in object_tuple_list:
            id, item_serialized, score = object_tuple
            if item_serialized is not None and decode_item:
                item = self.decode(item_serialized)
            else:
                item = item_serialized
            val_tuple = FifoObject(id, item, score)
            ret_list.append(val_tuple)
        mainLog.debug(
            f"mode={mode} minscore={minscore} maxscore={maxscore} count={count} protective={protective} temporary={temporary} decode_item={decode_item}"
        )
        return ret_list

    # get tuple of the first object and its score without dequeuing
    # If item is large un unnecessary to show int peek, set skip_item=True
    def peek(self, skip_item=False):
        mainLog = self.make_logger(_logger, f"id={self.fifoName}-{self.get_pid()}", method_name="peek")
        object_tuple = self.fifo.peek(skip_item=skip_item)
        if object_tuple is None:
            retVal = None
            mainLog.debug("fifo empty")
        else:
            id, item_serialized, score = object_tuple
            if item_serialized is None and score is None:
                retVal = FifoObject(None, None, None)
            else:
                if score is None:
                    score = time.time()
                retVal = FifoObject(id, item_serialized, score)
            mainLog.debug(f"score={score}")
        return retVal

    # get tuple of the last object and its score without dequeuing
    def peeklast(self, skip_item=False):
        mainLog = self.make_logger(_logger, f"id={self.fifoName}-{self.get_pid()}", method_name="peeklast")
        object_tuple = self.fifo.peeklast(skip_item=skip_item)
        if object_tuple is None:
            retVal = None
            mainLog.debug("fifo empty")
        else:
            id, item_serialized, score = object_tuple
            if item_serialized is None and score is None:
                retVal = FifoObject(None, None, None)
            else:
                if score is None:
                    score = time.time()
                retVal = FifoObject(id, item_serialized, score)
            mainLog.debug(f"score={score}")
        return retVal

    # get tuple of the object by id without dequeuing
    def peekbyid(self, id, temporary=False, skip_item=False):
        mainLog = self.make_logger(_logger, f"id={self.fifoName}-{self.get_pid()}", method_name="peekbyid")
        object_tuple = self.fifo.peekbyid(id, temporary, skip_item=skip_item)
        if object_tuple is None:
            retVal = None
            mainLog.debug("fifo empty")
        else:
            id_gotten, item_serialized, score = object_tuple
            if item_serialized is None and score is None:
                retVal = FifoObject(None, None, None)
            else:
                if score is None:
                    score = time.time()
                retVal = FifoObject(id, item_serialized, score)
            mainLog.debug(f"id={id} score={score} temporary={temporary}")
        return retVal

    # get list of object tuples without dequeuing
    def peekmany(self, mode="first", minscore=None, maxscore=None, count=None, skip_item=False):
        mainLog = self.make_logger(_logger, f"id={self.fifoName}-{self.get_pid()}", method_name="peekmany")
        object_tuple_list = self.fifo.peekmany(mode, minscore, maxscore, count, skip_item)
        if not object_tuple_list:
            mainLog.debug("empty list")
        ret_list = []
        for object_tuple in object_tuple_list:
            id_gotten, item_serialized, score = object_tuple
            if item_serialized is None and score is None:
                val_tuple = FifoObject(None, None, None)
            else:
                if score is None:
                    score = time.time()
                val_tuple = FifoObject(id, item_serialized, score)
            ret_list.append(val_tuple)
        mainLog.debug(f"mode={mode} minscore={minscore} maxscore={maxscore} count={count}")
        return ret_list

    # delete objects by list of ids from temporary space, return the number of objects successfully deleted
    def delete(self, ids):
        mainLog = self.make_logger(_logger, f"id={self.fifoName}-{self.get_pid()}", method_name="release")
        retVal = self.fifo.delete(ids)
        mainLog.debug(f"released {retVal} objects in {ids}")
        return retVal

    # restore objects by list of ids from temporary space to fifo; ids=None to restore all objects
    def restore(self, ids=None):
        mainLog = self.make_logger(_logger, f"id={self.fifoName}-{self.get_pid()}", method_name="restore")
        retVal = self.fifo.restore(ids)
        if ids is None:
            mainLog.debug("restored all objects")
        else:
            mainLog.debug(f"restored objects in {ids}")
        return retVal

    # update a object by its id with some conditions
    def update(self, id, item=None, score=None, temporary=None, cond_score="gt"):
        mainLog = self.make_logger(_logger, f"id={self.fifoName}-{self.get_pid()}", method_name="update")
        retVal = self.fifo.update(id, item, score, temporary, cond_score)
        update_report_list = []
        if item is not None:
            update_report_list.append(f"item={item}")
        if score is not None:
            update_report_list.append(f"score={score}")
        if temporary is not None:
            update_report_list.append(f"temporary={temporary}")
        update_report = " ".join(update_report_list)
        mainLog.debug(f"update id={id} cond_score={cond_score}: return={retVal}, {update_report}")
        return retVal


# Special fifo base for non havester-agent
class SpecialFIFOBase(FIFOBase):
    # constructor
    def __init__(self, **kwarg):
        FIFOBase.__init__(self, **kwarg)
        self.fifoName = f"{self.titleName}_fifo"
        pluginConf = {}
        pluginConf.update({"titleName": self.titleName})
        pluginConf.update(
            {
                "module": harvester_config.fifo.fifoModule,
                "name": harvester_config.fifo.fifoClass,
            }
        )
        pluginFactory = PluginFactory()
        self.fifo = pluginFactory.get_plugin(pluginConf)


# Benchmark fifo
class BenchmarkFIFO(SpecialFIFOBase):
    titleName = "benchmark"


# monitor fifo
class MonitorFIFO(FIFOBase):
    titleName = "monitor"

    # constructor
    def __init__(self, **kwarg):
        FIFOBase.__init__(self, **kwarg)
        self._initialize_fifo()

    def populate(self, seconds_ago=0, clear_fifo=False):
        """
        Populate monitor fifo with all active worker chunks and timeNow as score from DB
        with modificationTime earlier than seconds_ago seconds ago
        object in fifo = [(queueName_1, [[worker_1_1], [worker_1_2], ...]), (queueName_2, ...)]
        """
        if clear_fifo:
            self.fifo.clear()
        try:
            fifoMaxWorkersToPopulate = self.config.fifoMaxWorkersToPopulate
        except AttributeError:
            fifoMaxWorkersToPopulate = 2**32
        try:
            fifoMaxWorkersPerChunk = self.config.fifoMaxWorkersPerChunk
        except AttributeError:
            fifoMaxWorkersPerChunk = 500
        workspec_iterator = self.dbProxy.get_active_workers(fifoMaxWorkersToPopulate, seconds_ago)
        last_queueName = None
        workspec_chunk = []
        timeNow_timestamp = time.time()
        score = timeNow_timestamp
        for workspec in workspec_iterator:
            workspec.set_work_params({"lastCheckAt": timeNow_timestamp})
            if last_queueName is None:
                try:
                    score = timegm(workspec.modificationTime.utctimetuple())
                except Exception:
                    pass
                workspec_chunk = [[workspec]]
                last_queueName = workspec.computingSite
            elif workspec.computingSite == last_queueName and len(workspec_chunk) < fifoMaxWorkersPerChunk:
                workspec_chunk.append([workspec])
            else:
                self.put((last_queueName, workspec_chunk), score)
                try:
                    score = timegm(workspec.modificationTime.utctimetuple())
                except Exception:
                    pass
                workspec_chunk = [[workspec]]
                last_queueName = workspec.computingSite
        if len(workspec_chunk) > 0:
            self.put((last_queueName, workspec_chunk), score)

    def to_check_workers(self, check_interval=harvester_config.monitor.checkInterval):
        """
        Justify whether to check any worker by the modificationTime of the first worker in fifo
        retVal True if OK to dequeue to check;
        retVal False otherwise.
        Return retVal, overhead_time
        """
        mainLog = self.make_logger(_logger, f"id={self.fifoName}-{self.get_pid()}", method_name="to_check_worker")
        retVal = False
        overhead_time = None
        timeNow_timestamp = time.time()
        peeked_tuple = self.peek(skip_item=True)
        if peeked_tuple is not None:
            score = peeked_tuple.score
            overhead_time = timeNow_timestamp - score
            if overhead_time > 0:
                retVal = True
                if score < 0:
                    mainLog.debug("True. Preempting")
                    overhead_time = None
                else:
                    mainLog.debug("True")
                    mainLog.info(f"Overhead time is {overhead_time:.3f} sec")
            else:
                mainLog.debug("False. Workers too young to check")
                mainLog.debug(f"Overhead time is {overhead_time:.3f} sec")
        else:
            mainLog.debug("False. Got nothing in FIFO")
        return retVal, overhead_time


class MonitorEventFIFO(SpecialFIFOBase):
    titleName = "monitorEvent"

    # constructor
    def __init__(self, **kwarg):
        self.config = getattr(harvester_config, "monitor")
        self.enabled = False
        if hasattr(self.config, "fifoEnable") and self.config.fifoEnable and getattr(self.config, "eventBasedEnable", False):
            self.enabled = True
        SpecialFIFOBase.__init__(self, **kwarg)
