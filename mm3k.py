import datetime as dt
import os
import sys
import time
import pymongo
from bson.timestamp import Timestamp
from bson.objectid import ObjectId
import threading
import multiprocessing as mp
import hashlib
import argparse
import boto3
import warnings
from bson import encode
import math


def logIt(logName, logId, message, appConfig, targetClient):
    logTimeStamp = dt.datetime.now(dt.timezone.utc)
    logTimeStampString = logTimeStamp.isoformat()[:-3] + 'Z'
    durationSeconds = int(time.time() - appConfig['startTime'])
    d, durationSeconds = divmod(durationSeconds, 86400)
    h, durationSeconds = divmod(durationSeconds, 3600)
    m, s = divmod(durationSeconds, 60)
    durationString = f"{d:03d}:{h:02d}:{m:02d}:{s:02d}"
    print("{} | {} | {:>20} | {:>3d} | {}".format(logTimeStampString,durationString,logName,logId,message))
    targetClient[appConfig['mm3kDatabase']]['log'].insert_one({'processName':logName,'processId':logId,'message':message,'logTime':logTimeStamp})


def coordinator(appConfig,sourceClient,targetClient):
    # mm3k's project manager
    warnings.filterwarnings("ignore","You appear to be connected to a DocumentDB cluster.")

    logName = 'COORDINATOR'
    logId = 1

    allDone = False

    # check if there is a work in progress
    targetDb = targetClient[appConfig['mm3kDatabase']]
    statusColl = targetDb['status']
    processColl = targetDb['process']
    segmentsColl = targetDb['segments']

    feedbackSeconds = appConfig['feedbackSeconds']

    startTime = dt.datetime.fromtimestamp(time.time(),tz=dt.timezone.utc)

    statusColl.insert_one({'_id':1,'status':'RUNNING','totalCollections':0,'totalDocuments':0,'totalBytes':0,'totalSegments':0,'migratedCollections':0,'migratedDocuments':0,'migratedBytes':0,'migratedSegments':0,'startTime':startTime})
    processColl.insert_one({'type':logName,'id':logId,'status':'RUNNING','startTime':startTime})

    priorIntervalTime = time.time()
    priorMigratedDocuments = 0
    priorMigratedBytes = 0

    while not allDone:
        time.sleep(feedbackSeconds)

        runningProcesses = processColl.count_documents({'$and':[{'type':{'$ne':'COORDINATOR'}},{'status':{'$ne':'COMPLETED'}}]})
        if runningProcesses == 0:
            allDone = True
            continue

        result = statusColl.find_one({'_id':1})
        migratedDocuments = result['migratedDocuments']
        totalDocuments = result['totalDocuments']
        migratedBytes = result['migratedBytes']
        totalBytes = result['totalBytes']
        migratedSegments = result['migratedSegments']
        totalSegments = result['totalSegments']
        startTime = result['startTime']

        totElapsedSeconds = int(time.time() - startTime.timestamp())
        totDocumentsPerSecond = int(migratedDocuments / totElapsedSeconds)
        totBytesPerSecond = int(migratedBytes / totElapsedSeconds)
        totGigabitsPerSecond = totBytesPerSecond * 8 / (1024 ** 3)

        intElapsedSeconds = int(time.time() - priorIntervalTime)
        intDocuments = migratedDocuments - priorMigratedDocuments
        intBytes = migratedBytes - priorMigratedBytes
        if intElapsedSeconds == 0:
            intDocumentsPerSecond = 0
            intGigabitsPerSecond = 0
        else:
            intDocumentsPerSecond = int(intDocuments / intElapsedSeconds)
            intGigabitsPerSecond = intBytes * 8 / (1024 ** 3)

        logIt(logName,logId,"tot docs = {:,d} | tot migrated {:,d} docs at {:,d} ips | int migrated {:,d} docs at {:,d} ips | segments {:,d} of {:,d} | tot Gbps {:.2f} | int Gbps {:.2f} | procs {:,d}".format(totalDocuments,migratedDocuments,totDocumentsPerSecond,intDocuments,intDocumentsPerSecond,migratedSegments,totalSegments,totGigabitsPerSecond,intGigabitsPerSecond,runningProcesses),appConfig,targetClient)

        priorIntervalTime = time.time()
        priorMigratedDocuments = migratedDocuments
        priorMigratedBytes = migratedBytes
        
    endTime = dt.datetime.fromtimestamp(time.time(),tz=dt.timezone.utc)
    processColl.update_one({'type':logName,'id':logId},{'$set':{'status':'COMPLETED','endTime':endTime}})

    result = statusColl.find_one({'_id':1})
    migratedDocuments = result['migratedDocuments']
    migratedBytes = result['migratedBytes']
    startTime = result['startTime']

    totElapsedSeconds = int(time.time() - startTime.timestamp())
    totDocumentsPerSecond = int(migratedDocuments / totElapsedSeconds)
    totBytesPerSecond = int(migratedBytes / totElapsedSeconds)
    totGigabitsPerSecond = totBytesPerSecond * 8 / (1024 ** 3)
    totTerabytesPerHour = totBytesPerSecond * 3600 / 1e12

    logIt(logName,logId,"migration complete | {:,d} seconds | {:,d} docs | {:,d} ips | {:.2f} TB/hr".format(totElapsedSeconds,migratedDocuments,totDocumentsPerSecond,totTerabytesPerHour),appConfig,targetClient) 

    # load performance by collection
    result = segmentsColl.aggregate([{'$group':{'_id':{'database':'$database','collection':'$collection'},
                                                'avgObjSize':{'$avg':'$avgObjSize'},
                                                'numSegments':{'$sum':1},
                                                'numDocuments':{'$sum':'$loadDocuments'},
                                                'numBytes':{'$sum':'$loadBytes'},
                                                'numSeconds':{'$sum':'$loadSeconds'}}},
                                     {'$project':{'averageIps':{'$divide':['$numDocuments','$numSeconds']},'numSegments':1,'numDocuments':1,'numSeconds':1,'avgObjSize':1,'numBytes':1}},
                                     {'$sort':{'_id':1}}])

    for thisResult in result:
        thisGb = thisResult['numBytes'] / (1024 ** 3)
        logIt(logName,logId,"collection perf | {}.{} | {:,.2f} GB | {:,d} tot seconds | {:,d} docs | {:,d} segments | {:,d} ips | {:,d} avgObjSize".format(thisResult['_id']['database'],thisResult['_id']['collection'],thisGb,int(thisResult['numSeconds']),int(thisResult['numDocuments']),int(thisResult['numSegments']),int(thisResult['averageIps']),int(thisResult['avgObjSize'])),appConfig,targetClient)


def catalogger(appConfig,sourceClient,targetClient):
    # catalog the effort - just namespaces
    warnings.filterwarnings("ignore","You appear to be connected to a DocumentDB cluster.")

    logName = 'CATALOGGER'
    logId = 1

    targetDb = targetClient[appConfig['mm3kDatabase']]
    targetColl = targetDb['collections']
    statusColl = targetDb['status']
    processColl = targetDb['process']

    startTime = dt.datetime.fromtimestamp(time.time(),tz=dt.timezone.utc)
    processColl.insert_one({'type':logName,'id':logId,'status':'RUNNING','startTime':startTime})

    dbDict = sourceClient.admin.command("listDatabases",nameOnly=True,filter={"name":{"$nin":['admin','config','local','system']}})['databases']
    for thisDb in dbDict:
        if thisDb['name'] in [appConfig['mm3kDatabase']]:
            logIt(logName,logId,"*** SKIPPING mm3k state database {}".format(thisDb['name']),appConfig,targetClient)
            continue

        #if thisDb['name'] not in ['dmschart']:
        #    logIt(logName,logId,"*** SKIPPING database {}".format(thisDb['name']),appConfig,targetClient)
        #    continue

        logIt(logName,logId,"catalogging database {}".format(thisDb['name']),appConfig,targetClient)
        collCursor = sourceClient[thisDb['name']].list_collections()
        for thisColl in collCursor:
            #print(thisColl)
            result = targetColl.insert_one({'database':thisDb['name'],'collection':thisColl['name'],'status':'CATALOGGED'})
            result = statusColl.update_one({'_id':1},{'$inc':{'totalCollections':1}})

    endTime = dt.datetime.fromtimestamp(time.time(),tz=dt.timezone.utc)
    processColl.update_one({'type':logName,'id':logId},{'$set':{'status':'COMPLETED','endTime':endTime}})
    logIt(logName,logId,"COMPLETED - stopping",appConfig,targetClient)


def inspector(appConfig,sourceClient,targetClient):
    # catalog the effort - namespaces and their document count, average document size, size on disk
    warnings.filterwarnings("ignore","You appear to be connected to a DocumentDB cluster.")

    logName = 'INSPECTOR'
    logId = 1

    chunkBytesTarget = appConfig['chunkBytesTarget']

    targetDb = targetClient[appConfig['mm3kDatabase']]
    targetColl = targetDb['collections']
    statusColl = targetDb['status']
    processColl = targetDb['process']

    numWorkCheckAttempts = appConfig['numWorkCheckAttempts']
    numWorkCheckSecondsBetween = appConfig['numWorkCheckSecondsBetween']

    startTime = dt.datetime.fromtimestamp(time.time(),tz=dt.timezone.utc)
    processColl.insert_one({'type':logName,'id':logId,'status':'RUNNING','startTime':startTime})

    allDone = False
    numNoDocuments = 0

    # loop through catalogged collections
    while not allDone:
        thisCollection = targetColl.find_one_and_update({'status':'CATALOGGED'},{'$set':{'status':'INSPECTING'}})
        if thisCollection == None:
            # check if any remaining collections needing inspecting and if any cataloggers are still running
            uninspectedCollections = targetColl.count_documents({'status':'CATALOGGED'})
            runningCataloggers = processColl.count_documents({'type':'CATALOGGER','status':{'$ne':'COMPLETED'}})

            if (uninspectedCollections != 0) or (runningCataloggers != 0):
                if appConfig['verboseLogging']:
                    logIt(logName,logId,'no work found but {} uninspected collections and {} running cataloggers'.format(uninspectedCollections,runningCataloggers),appConfig,targetClient)
                time.sleep(numWorkCheckSecondsBetween)
            else:
                allDone = True

            continue

        logIt(logName,logId,'inspecting {}.{}'.format(thisCollection['database'],thisCollection['collection']),appConfig,targetClient)
        numNoDocuments = 0

        db = sourceClient[thisCollection['database']]
        col = db[thisCollection['collection']]
        collStats = db.command("collStats",thisCollection['collection'])
        numDocuments = collStats['count']
        avgObjSize = max(int(collStats['avgObjSize']),1)
        rowsPerChunk = int(chunkBytesTarget / avgObjSize)
        size = collStats['size']
        storageSize = collStats['storageSize']

        # get min _id, max _id, and _id data types
        idFirst = col.aggregate([{"$sort":{"_id":pymongo.ASCENDING}},{"$project":{"_id":True,"idType":{"$type":"$_id"}}},{"$limit":1}]).next()
        idLast = col.aggregate([{"$sort":{"_id":pymongo.DESCENDING}},{"$project":{"_id":True,"idType":{"$type":"$_id"}}},{"$limit":1}]).next()

        targetColl.update_one({'_id':thisCollection['_id']},
                              {'$set':{'status':'INSPECTED',
                                       'numDocuments':numDocuments,
                                       'avgObjSize':avgObjSize,
                                       'rowsPerChunk':rowsPerChunk,
                                       'size':size,
                                       'storageSize':storageSize,
                                       'minId':idFirst['_id'],
                                       'minIdType':idFirst['idType'],
                                       'maxId':idLast['_id'],
                                       'maxIdType':idLast['idType']}})

        statusColl.update_one({'_id':1},{'$inc':{'totalDocuments':numDocuments,'totalBytes':size}})

    endTime = dt.datetime.fromtimestamp(time.time(),tz=dt.timezone.utc)
    processColl.update_one({'type':logName,'id':logId},{'$set':{'status':'COMPLETED','endTime':endTime}})
    logIt(logName,logId,"COMPLETED - stopping",appConfig,targetClient)


def segmenter(appConfig,threadNum,sourceClient,targetClient):
    # catalog the effort - namespaces and their document count, average document size, size on disk
    warnings.filterwarnings("ignore","You appear to be connected to a DocumentDB cluster.")

    logName = 'SEGMENTER'
    logId = threadNum 

    targetDb = targetClient[appConfig['mm3kDatabase']]
    targetColl = targetDb['collections']
    targetCollSegments = targetDb['segments']
    statusColl = targetDb['status']
    processColl = targetDb['process']

    numWorkCheckAttempts = appConfig['numWorkCheckAttempts']
    numWorkCheckSecondsBetween = appConfig['numWorkCheckSecondsBetween']

    startTime = dt.datetime.fromtimestamp(time.time(),tz=dt.timezone.utc)
    processColl.insert_one({'type':logName,'id':logId,'status':'RUNNING','startTime':startTime})

    allDone = False
    numNoDocuments = 0

    # loop through inspected collections
    while not allDone:
        thisCollection = targetColl.find_one_and_update({'status':'INSPECTED'},{'$set':{'status':'SEGMENTING'}})
        startTime = time.time()
        if thisCollection == None:
            # wait and try again
            if appConfig['verboseLogging']:
                logIt(logName,logId,'no work found # {}'.format(numNoDocuments),appConfig,targetClient)
            numNoDocuments += 1
            if numNoDocuments >= numWorkCheckAttempts:
                allDone = True
            else:
                time.sleep(numWorkCheckSecondsBetween)
            continue
        logIt(logName,logId,'segmenting {}.{}'.format(thisCollection['database'],thisCollection['collection']),appConfig,targetClient)
        numNoDocuments = 0

        # we have a collection to segment
        if appConfig['mathSegments'] and (thisCollection['minIdType'] != 'objectId' or thisCollection['maxIdType'] != 'objectId'):
            # math segmentation only available for pure objectId _id collections
            logIt(logName,logId,'math segmenting not allowed for {}.{} | {} to {} _id datatypes not supported | performing old school segmenting'.format(thisCollection['database'],thisCollection['collection'],thisCollection['minIdType'],thisCollection['maxIdType']),appConfig,targetClient)
            numSegments = segmentCollectionOldSchool(appConfig,thisCollection,sourceClient,targetDb,threadNum,targetClient)
        elif appConfig['mathSegments']:
            numSegments = segmentCollectionUsingMaths(appConfig,thisCollection,sourceClient,targetDb,threadNum,targetClient)
        else:
            numSegments = segmentCollectionOldSchool(appConfig,thisCollection,sourceClient,targetDb,threadNum,targetClient)
        endTime = time.time()

        targetColl.update_one({'_id':thisCollection['_id']},
                              {'$set':{'status':'SEGMENTED',
                                       'numSegments':numSegments,
                                       'segmentStartTime':dt.datetime.fromtimestamp(startTime,tz=dt.timezone.utc),
                                       'segmentEndTime':dt.datetime.fromtimestamp(endTime,tz=dt.timezone.utc),
                                       'segmentSeconds':endTime-startTime}})

    endTime = dt.datetime.fromtimestamp(time.time(),tz=dt.timezone.utc)
    processColl.update_one({'type':logName,'id':logId},{'$set':{'status':'COMPLETED','endTime':endTime}})
    logIt(logName,logId,"COMPLETED - stopping",appConfig,targetClient)
        

def segmentCollectionOldSchool(appConfig,thisCollection,sourceClient,targetDb,threadNum,targetClient):
    # get boundaries by performing server-side skips
    warnings.filterwarnings("ignore","You appear to be connected to a DocumentDB cluster.")

    logName = 'SEGMENT-COLLECTION'
    logId = threadNum

    sourceDb = thisCollection['database']
    sourceColl = thisCollection['collection']

    col = sourceClient[sourceDb][sourceColl]
    targetColl = targetDb['segments']
    statusColl = targetDb['status']

    chunkGbTarget = appConfig['chunkGbTarget']

    numDocuments = thisCollection['numDocuments']
    avgObjSize = thisCollection['avgObjSize']
    rowsPerChunk = thisCollection['rowsPerChunk']

    logIt(logName,logId,"collection {}.{} contains {} documents".format(sourceDb,sourceColl,numDocuments),appConfig,targetClient)
    logIt(logName,logId,"calculated {} documents for a {} GB chunk of {} average object (bytes)".format(rowsPerChunk,chunkGbTarget,avgObjSize),appConfig,targetClient)
    logIt(logName,logId,"segmenting {}.{} via skips".format(sourceDb,sourceColl),appConfig,targetClient)

    allDone = False

    queryStartTime = time.time()

    # get the first and last _id
    minId = thisCollection['minId']
    maxId = thisCollection['maxId']
    currentId = minId
    priorId = minId

    numDocsTotal = 0
    numBoundaries = 0

    while not allDone:
        startTime = time.time()
        currentId = col.find_one(filter={"_id":{"$gt":currentId}},projection={"_id":True},sort=[("_id",pymongo.ASCENDING)],skip=rowsPerChunk)
        endTime = time.time()
        numSeconds = endTime - startTime

        # no more boundaries
        if currentId is None:
            # create final segment
            if numBoundaries == 0:
                # single segment collection
                result = targetColl.insert_one({'database':sourceDb,'collection':sourceColl,'segment':1,'minId':minId,'maxId':maxId,'segmentStartTime':dt.datetime.fromtimestamp(startTime,tz=dt.timezone.utc),'segmentEndTime':dt.datetime.fromtimestamp(endTime,tz=dt.timezone.utc),'segmentSeconds':numSeconds,'status':'SEGMENTED','avgObjSize':avgObjSize})
            else:
                # multiple segment collection
                result = targetColl.insert_one({'database':sourceDb,'collection':sourceColl,'segment':numBoundaries+1,'minId':priorId,'maxId':maxId,'segmentStartTime':dt.datetime.fromtimestamp(startTime,tz=dt.timezone.utc),'segmentEndTime':dt.datetime.fromtimestamp(endTime,tz=dt.timezone.utc),'segmentSeconds':numSeconds,'status':'SEGMENTED','avgObjSize':avgObjSize})

            result = statusColl.update_one({'_id':1},{'$inc':{'totalSegments':1}})
            allDone = True
            continue
        else:
            # create segment
            result = targetColl.insert_one({'database':sourceDb,'collection':sourceColl,'segment':numBoundaries+1,'minId':priorId,'maxId':currentId['_id'],'segmentStartTime':dt.datetime.fromtimestamp(startTime,tz=dt.timezone.utc),'segmentEndTime':dt.datetime.fromtimestamp(endTime,tz=dt.timezone.utc),'segmentSeconds':numSeconds,'status':'SEGMENTED','avgObjSize':avgObjSize})
            result = statusColl.update_one({'_id':1},{'$inc':{'totalSegments':1}})

        priorId = currentId['_id']
        numDocsTotal += rowsPerChunk
        pctDone = numDocsTotal/(numDocuments - rowsPerChunk)*100
        elapsedSecs = int(time.time() - queryStartTime)
        estimatedSecsToDone = max(0,int(((100/pctDone)*elapsedSecs)-elapsedSecs))
        numBoundaries += 1
        logIt(logName,logId,"ns {}.{} | boundary {:3d} - {} {} | done in approximately {} seconds".format(sourceDb,sourceColl,numBoundaries,type(currentId),currentId,estimatedSecsToDone),appConfig,targetClient)

    return numBoundaries+1


def segmentCollectionUsingMaths(appConfig,thisCollection,sourceClient,targetDb,threadNum,targetClient):
    # get boundaries by mathematically chunking the keyspace
    warnings.filterwarnings("ignore","You appear to be connected to a DocumentDB cluster.")

    logName = 'SEGMENT-COLLECTION'
    logId = threadNum

    sourceDb = thisCollection['database']
    sourceColl = thisCollection['collection']

    col = sourceClient[sourceDb][sourceColl]
    targetColl = targetDb['segments']
    statusColl = targetDb['status']

    chunkGbTarget = appConfig['chunkGbTarget']

    numDocuments = thisCollection['numDocuments']
    avgObjSize = thisCollection['avgObjSize']
    rowsPerChunk = thisCollection['rowsPerChunk']
    size = thisCollection['size']
    numCalculatedSegments = int(size / (chunkGbTarget * (1024 ** 3)))+1

    logIt(logName,logId,"collection {}.{} contains {} documents".format(sourceDb,sourceColl,numDocuments),appConfig,targetClient)
    logIt(logName,logId,"calculated {} documents for a {} GB chunk of {} average object (bytes)".format(rowsPerChunk,chunkGbTarget,avgObjSize),appConfig,targetClient)
    logIt(logName,logId,"segmenting {}.{} mathematically into {} segments".format(sourceDb,sourceColl,numCalculatedSegments),appConfig,targetClient)

    allDone = False

    queryStartTime = time.time()

    # get the first and last _id
    minId = thisCollection['minId']
    maxId = thisCollection['maxId']

    startTime = time.time()

    if numCalculatedSegments == 1:
        result = targetColl.insert_one({'database':sourceDb,'collection':sourceColl,'segment':1,'minId':minId,'maxId':maxId,'segmentStartTime':dt.datetime.fromtimestamp(startTime,tz=dt.timezone.utc),'segmentEndTime':dt.datetime.fromtimestamp(startTime,tz=dt.timezone.utc),'segmentSeconds':0,'status':'SEGMENTED','avgObjSize':avgObjSize})
    else:
        # calculate the segments
        intMinId = int(str(minId),16)
        intMaxId = int(str(maxId),16)
        idDiff = int(intMaxId - intMinId)
        idDiffInc = int(idDiff / numCalculatedSegments)
        intPriorId = intMinId
        for loop in range(numCalculatedSegments):
            result = targetColl.insert_one({'database':sourceDb,'collection':sourceColl,'segment':loop+1,'minId':ObjectId(hex(intPriorId)[2:].zfill(24)),'maxId':ObjectId(hex(intPriorId+idDiffInc)[2:].zfill(24)),'segmentStartTime':dt.datetime.fromtimestamp(startTime,tz=dt.timezone.utc),'segmentEndTime':dt.datetime.fromtimestamp(startTime,tz=dt.timezone.utc),'segmentSeconds':0,'status':'SEGMENTED','avgObjSize':avgObjSize})
            intPriorId += idDiffInc
        if intPriorId < intMaxId:
            # create final segment
            numCalculatedSegments += 1
            result = targetColl.insert_one({'database':sourceDb,'collection':sourceColl,'segment':numCalculatedSegments,'minId':ObjectId(hex(intPriorId)[2:].zfill(24)),'maxId':maxId,'segmentStartTime':dt.datetime.fromtimestamp(startTime,tz=dt.timezone.utc),'segmentEndTime':dt.datetime.fromtimestamp(startTime,tz=dt.timezone.utc),'segmentSeconds':0,'status':'SEGMENTED','avgObjSize':avgObjSize})

    result = statusColl.update_one({'_id':1},{'$inc':{'totalSegments':numCalculatedSegments}})

    return numCalculatedSegments


def loader(processNum, appConfig):
    # load segments
    warnings.filterwarnings("ignore","You appear to be connected to a DocumentDB cluster.")

    logName = 'LOADER'
    logId = processNum

    sourceClient = pymongo.MongoClient(host=appConfig['sourceUri'])
    targetClient = pymongo.MongoClient(host=appConfig['targetUri'])
    targetDb = targetClient[appConfig['mm3kDatabase']]
    targetColl = targetDb['collections']
    targetCollSegments = targetDb['segments']
    statusColl = targetDb['status']
    processColl = targetDb['process']

    numWorkCheckAttempts = appConfig['numWorkCheckAttempts']
    numWorkCheckSecondsBetween = appConfig['numWorkCheckSecondsBetween']

    startTime = dt.datetime.fromtimestamp(time.time(),tz=dt.timezone.utc)
    processColl.insert_one({'type':logName,'id':logId,'status':'RUNNING','startTime':startTime})

    dryRun = appConfig['dryRun']

    allDone = False
    numNoDocuments = 0

    # loop through inspected collections
    while not allDone:
        thisSegment = targetCollSegments.find_one_and_update({'status':'SEGMENTED'},{'$set':{'status':'LOADING'}})
        startTime = time.time()
        if thisSegment == None:
            # wait and try again
            if appConfig['verboseLogging']:
                logIt(logName,logId,'no work found # {}'.format(numNoDocuments),appConfig,targetClient)
            numNoDocuments += 1
            if numNoDocuments >= numWorkCheckAttempts:
                allDone = True
            else:
                time.sleep(numWorkCheckSecondsBetween)
            continue

        logIt(logName,logId,'started loading segment {} of {}.{}'.format(thisSegment['segment'],thisSegment['database'],thisSegment['collection']),appConfig,targetClient)

        # we have a segment to load
        numDocumentsLoaded,numBytesLoaded = loadSegment(appConfig,thisSegment,sourceClient,targetClient,processNum,dryRun)
        endTime = time.time()

        targetCollSegments.update_one({'_id':thisSegment['_id']},
                              {'$set':{'status':'LOADED',
                                       'loadDocuments':numDocumentsLoaded,
                                       'loadBytes':numBytesLoaded,
                                       'loadStartTime':dt.datetime.fromtimestamp(startTime,tz=dt.timezone.utc),
                                       'loadEndTime':dt.datetime.fromtimestamp(endTime,tz=dt.timezone.utc),
                                       'loadSeconds':endTime-startTime}})

        statusColl.update_one({'_id':1},{'$inc':{'migratedDocuments':numDocumentsLoaded,'migratedBytes':numBytesLoaded,'migratedSegments':1}})

        logIt(logName,logId,'finished loading segment {} of {}.{}'.format(thisSegment['segment'],thisSegment['database'],thisSegment['collection']),appConfig,targetClient)

    endTime = dt.datetime.fromtimestamp(time.time(),tz=dt.timezone.utc)
    processColl.update_one({'type':logName,'id':logId},{'$set':{'status':'COMPLETED','endTime':endTime}})
    logIt(logName,logId,"COMPLETED - stopping",appConfig,targetClient)

    sourceClient.close()
    targetClient.close()


def loadSegment(appConfig,thisSegment,sourceClient,targetClient,processNum,dryRun):
    # load a single segment
    warnings.filterwarnings("ignore","You appear to be connected to a DocumentDB cluster.")

    logName = 'LOAD-SEGMENT'
    logId = processNum

    sourceDb = sourceClient[thisSegment['database']]
    sourceColl = sourceDb[thisSegment['collection']]

    targetDb = targetClient[thisSegment['database']]
    targetColl = targetDb[thisSegment['collection']]

    loadMetricsColl = targetClient[appConfig['mm3kDatabase']]['loadMetrics']
    #loadMetricsColl = targetDb['loadMetrics']

    loadMetricInterval = appConfig['loadMetricInterval']
    loadMetricFeedback = appConfig['loadMetricFeedback']

    startTime = time.time()
    nextPerfReportTime = startTime + loadMetricFeedback

    bulkOpList = []

    numCurrentBulkOps = 0
    numTotalBytes = 0
    numTotalBatches = 0
    numTotalInserts = 0
    numIntervalBatches = 0
    numIntervalInserts = 0
    numIntervalBytes = 0

    boundaryFieldName = '_id'

    # special query for first segment, need to include minId
    if thisSegment['segment'] == 1:
        cursor = sourceColl.find({boundaryFieldName: {'$gte': thisSegment['minId'], '$lte': thisSegment['maxId']}})
    else:
        cursor = sourceColl.find({boundaryFieldName: {'$gt': thisSegment['minId'], '$lte': thisSegment['maxId']}})

    for doc in cursor:
        numTotalInserts += 1
        numIntervalInserts += 1
        numDocBytes = len(encode(doc))
        numTotalBytes += numDocBytes
        numIntervalBytes += numDocBytes
        numCurrentBulkOps += 1
        bulkOpList.append(pymongo.InsertOne(doc))

        if (numCurrentBulkOps >= appConfig["maxInsertsPerBatch"]):
            if not dryRun:
                result = targetColl.bulk_write(bulkOpList,ordered=False)
            bulkOpList = []
            numCurrentBulkOps = 0
            numTotalBatches += 1
            numIntervalBatches += 1

        if time.time() > nextPerfReportTime:
            nextPerfReportTime = time.time() + loadMetricFeedback
            # find next "second" boundary
            dtSecondBoundary = dt.datetime.fromtimestamp(math.ceil(time.time() / loadMetricInterval) * loadMetricInterval,tz=dt.timezone.utc)
            try:
                result = loadMetricsColl.update_one({"_id":dtSecondBoundary},{"$setOnInsert":{"seconds":loadMetricInterval},"$inc":{"batches":numIntervalBatches,"inserts":numIntervalInserts,"bytes":numIntervalBytes}},upsert=True)
            except pymongo.errors.DuplicateKeyError:
                result = loadMetricsColl.update_one({"_id":dtSecondBoundary},{"$setOnInsert":{"seconds":loadMetricInterval},"$inc":{"batches":numIntervalBatches,"inserts":numIntervalInserts,"bytes":numIntervalBytes}},upsert=True)

            numIntervalBatches = 0
            numIntervalInserts = 0
            numIntervalBytes = 0

    if (numCurrentBulkOps > 0):
        if not dryRun:
            result = targetColl.bulk_write(bulkOpList,ordered=False)
        bulkOpList = []
        numCurrentBulkOps = 0
        numTotalBatches += 1

    # log final load metrics
    dtSecondBoundary = dt.datetime.fromtimestamp(math.ceil(time.time() / loadMetricInterval) * loadMetricInterval,tz=dt.timezone.utc)
    try:
        result = loadMetricsColl.update_one({"_id":dtSecondBoundary},{"$setOnInsert":{"seconds":loadMetricInterval},"$inc":{"batches":numIntervalBatches,"inserts":numIntervalInserts,"bytes":numIntervalBytes}},upsert=True)
    except:
        result = loadMetricsColl.update_one({"_id":dtSecondBoundary},{"$setOnInsert":{"seconds":loadMetricInterval},"$inc":{"batches":numIntervalBatches,"inserts":numIntervalInserts,"bytes":numIntervalBytes}},upsert=True)

    return numTotalInserts, numTotalBytes


def main():
    parser = argparse.ArgumentParser(description='Mongo migrator 3000')

    parser.add_argument('--source-uri',required=True,type=str,help='Source URI')
    parser.add_argument('--target-uri',required=True,type=str,help='Target URI')
    parser.add_argument('--verbose',required=False,action='store_true',help='Enable verbose logging')
    parser.add_argument('--num-full-load-workers',required=False,default=10,type=int,help='Number of workers performing full load')
    parser.add_argument('--chunk-gb-target',required=False,default=1.0,type=float,help='Target/maximum size for each full load chunk in gigabytes')
    parser.add_argument('--mm3k-database',required=False,type=str,default='mm3k-state',help='Source URI')

    parser.add_argument('--num-segmenters',required=False,type=int,default=10,help='Maximum number of concurrent segmenters')
    parser.add_argument('--num-loaders',required=False,type=int,default=10,help='Maxiumum number of concurrent loadersd')
                        
    #parser.add_argument('--source-namespace',required=True,type=str,help='Source Namespace as <database>.<collection>')
    #parser.add_argument('--target-namespace',required=False,type=str,help='Target Namespace as <database>.<collection>, defaults to --source-namespace')
    parser.add_argument('--feedback-seconds',required=False,type=int,default=60,help='Number of seconds between feedback output')
    parser.add_argument('--max-inserts-per-batch',required=False,type=int,default=200,help='Maximum number of inserts to include in a single batch')
    parser.add_argument('--dry-run',required=False,action='store_true',help='Read only, do not apply to target (except --mm3k-database)')
    #parser.add_argument('--create-cloudwatch-metrics',required=False,action='store_true',help='Create CloudWatch metrics')
    #parser.add_argument('--cluster-name',required=False,type=str,help='Name of cluster for CloudWatch metrics')

    parser.add_argument('--math-segments',required=False,action='store_true',help='Calculate segments using math, not queries')

    args = parser.parse_args()

    MIN_PYTHON = (3, 7)
    if (sys.version_info < MIN_PYTHON):
        sys.exit("\nPython %s.%s or later is required.\n" % MIN_PYTHON)

    #if args.create_cloudwatch_metrics and (args.cluster_name is None):
    #    sys.exit("\nMust supply --cluster-name when capturing CloudWatch metrics.\n")

    appConfig = {}
    appConfig['sourceUri'] = args.source_uri
    appConfig['targetUri'] = args.target_uri
    #appConfig['sourceNs'] = args.source_namespace
    #if not args.target_namespace:
    #    appConfig['targetNs'] = args.source_namespace
    #else:
    #    appConfig['targetNs'] = args.target_namespace
    appConfig['verboseLogging'] = args.verbose
    appConfig['numFullLoadWorkers'] = int(args.num_full_load_workers)
    appConfig['chunkGbTarget'] = float(args.chunk_gb_target)
    appConfig['chunkBytesTarget'] = int(args.chunk_gb_target * (1024 ** 3))
    appConfig['mm3kDatabase'] = args.mm3k_database
    appConfig['numSegmenters'] = args.num_segmenters
    appConfig['numLoaders'] = args.num_loaders
    appConfig['maxInsertsPerBatch'] = args.max_inserts_per_batch
    appConfig['feedbackSeconds'] = args.feedback_seconds
    appConfig['dryRun'] = args.dry_run
    appConfig['mathSegments'] = args.math_segments
    #appConfig['createCloudwatchMetrics'] = args.create_cloudwatch_metrics
    #appConfig['clusterName'] = args.cluster_name
    
    # collect load metrics reported at this number of seconds of granularity
    appConfig['loadMetricInterval'] = 10
    # segment loaders report to the current interval every this many seconds
    appConfig['loadMetricFeedback'] = 4
    # start time
    appConfig['startTime'] = time.time()
    # number of attempts to find work for any mm3k process
    appConfig['numWorkCheckAttempts'] = 12
    # number of seconds between work available checks
    appConfig['numWorkCheckSecondsBetween'] = 6

    sourceClient = pymongo.MongoClient(host=appConfig['sourceUri'])
    targetClient = pymongo.MongoClient(host=appConfig['targetUri'])

    mp.set_start_method('spawn')
    #q = mp.Manager().Queue()

    tCoordinator = threading.Thread(target=coordinator,args=(appConfig,sourceClient,targetClient,))
    tCoordinator.start()

    tCatalogger = threading.Thread(target=catalogger,args=(appConfig,sourceClient,targetClient,))
    tCatalogger.start()

    tInspector = threading.Thread(target=inspector,args=(appConfig,sourceClient,targetClient,))
    tInspector.start()

    segmenterList = []
    for loop in range(appConfig['numSegmenters']):
        tSegmenter = threading.Thread(target=segmenter,args=(appConfig,loop,sourceClient,targetClient))
        tSegmenter.start()
        segmenterList.append(tSegmenter)

    loaderList = []
    for loop in range(appConfig["numLoaders"]):
        pLoader = mp.Process(target=loader,args=(loop,appConfig,))
        loaderList.append(pLoader)
        pLoader.start()
        
    for pLoader in loaderList:
        pLoader.join()

    for thisSegmenter in segmenterList:
        thisSegmenter.join()
    tInspector.join()
    tCatalogger.join()
    tCoordinator.join()

    sourceClient.close()
    targetClient.close()


if __name__ == "__main__":
    main()
