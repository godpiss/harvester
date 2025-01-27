#!/bin/env python

import os
import re

from future.utils import iteritems
from pandalogger import logger_config

logdir = logger_config.daemon["logdir"]

# collect data
data = dict()
with open(os.path.join(logdir, "panda-db_proxy_pool.log")) as f:
    for line in f:
        m = re.search("<method=([^>]+)> release lock .* (\d+\.\d+) ", line)
        if m is not None:
            method = m.group(1)
            exeTime = float(m.group(2))
            if method not in data:
                data[method] = []
            data[method].append(exeTime)

nS = 20
nL = 20

# get average and longest
lData = dict()
aData = dict()
for method, exeTimes in iteritems(data):
    exeTimes.sort()
    exeTimes.reverse()
    for exeTime in exeTimes[:nL]:
        if exeTime not in lData:
            lData[exeTime] = set()
        lData[exeTime].add(method)
    ave = sum(exeTimes) / float(len(exeTimes))
    if ave not in aData:
        aData[ave] = []
    aData[ave].append(method)

# show average
aveList = sorted(aData.keys())
aveList.reverse()

print
print(f"Execution time summary : top {nS}")
print(" average      max   n_call  method")
print("-------------------------------------------------")
i = 0
escape = False
for ave in aveList:
    for method in aData[ave]:
        print(f"{ave:8.2f} {max(data[method]):8.2f} {len(data[method]):8d}  {method}")
        i += 1
        if i >= nS:
            escape = True
            break
    if escape:
        break

# show longest methods
longList = sorted(lData.keys())
longList.reverse()

print
print(f"Long execution method : top {nL}")
print(" method                           time")
print("-------------------------------------------------")
i = 0
escape = False
for val in longList:
    for method in lData[val]:
        print(f" {method:30} {val:8.2f}")
        i += 1
        if i >= nL:
            escape = True
            break
    if escape:
        break
