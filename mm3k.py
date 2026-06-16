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


def logIt(logName, logId, message):
    logTimeStamp = dt.datetime.now(dt.timezone.utc).isoformat()[:-3] + 'Z'
    print("[{}] {:>20} | {:>3d} | {}".format(logTimeStamp,logName,logId,message))


def coordinator(appConfig):
    # mm3k's project manager
    warnings.filterwarnings("ignore","You appear to be connected to a DocumentDB cluster.")

    logName = 'COORDINATOR'
    logId = 1

    allDone = False

    # check if there is a work in progress
    targetClient = pymongo.MongoClient(host=appConfig['targetUri'],appname='mm3k')
    targetDb = targetClient[appConfig['mm3kDatabase']]
    statusColl = targetDb['status']

    startTime = dt.datetime.fromtimestamp(time.time(),tz=dt.timezone.utc)

    statusColl.insert_one({'_id':1,'status':'RUNNING','totalCollections':0,'totalDocuments':0,'totalBytes':0,'totalSegments':0,'migratedCollections':0,'migratedDocuments':0,'migratedBytes':0,'migratedSegments':0,'startTime':startTime})

    # does the database already exist
    
    # if so we must be resuming, not starting

    # get things started - cataloggers

    # get things started - segmenters

    # get things started - dataLoaders

    # wait for all children threads and processes to be gone

    priorIntervalTime = time.time()
    priorMigratedDocuments = 0
    priorMigratedBytes = 0

    while not allDone:
        time.sleep(10)
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

        logIt(logName,logId,"tot docs = {:,d} | tot migrated {:,d} docs at {:,d} ips | int migrated {:,d} docs at {:,d} ips | segments {:,d} of {:,d} | tot Gbps {:.2f} | int Gbps {:.2f}".format(totalDocuments,migratedDocuments,totDocumentsPerSecond,intDocuments,intDocumentsPerSecond,migratedSegments,totalSegments,totGigabitsPerSecond,intGigabitsPerSecond))

        priorIntervalTime = time.time()
        priorMigratedDocuments = migratedDocuments
        priorMigratedBytes = migratedBytes
        
    targetClient.close()


def catalogger(appConfig):
    # catalog the effort - just namespaces
    warnings.filterwarnings("ignore","You appear to be connected to a DocumentDB cluster.")

    logName = 'CATALOGGER'
    logId = 1

    sourceClient = pymongo.MongoClient(host=appConfig['sourceUri'])
    targetClient = pymongo.MongoClient(host=appConfig['targetUri'])
    targetDb = targetClient[appConfig['mm3kDatabase']]
    targetColl = targetDb['collections']
    statusColl = targetDb['status']

    dbDict = sourceClient.admin.command("listDatabases",nameOnly=True,filter={"name":{"$nin":['admin','config','local','system']}})['databases']
    for thisDb in dbDict:
        if thisDb['name'] == 'dms4':
            # //tmc skipping for now
            logIt(logName,logId,"*** SKIPPING database {}".format(thisDb['name']))
            continue

        logIt(logName,logId,"catalogging database {}".format(thisDb['name']))
        collCursor = sourceClient[thisDb['name']].list_collections()
        for thisColl in collCursor:
            #print(thisColl)
            result = targetColl.insert_one({'database':thisDb['name'],'collection':thisColl['name'],'status':'CATALOGGED'})
            result = statusColl.update_one({'_id':1},{'$inc':{'totalCollections':1}})

    sourceClient.close()
    targetClient.close()


def inspector(appConfig):
    # catalog the effort - namespaces and their document count, average document size, size on disk
    warnings.filterwarnings("ignore","You appear to be connected to a DocumentDB cluster.")

    logName = 'INSPECTOR'
    logId = 1

    chunkGbTarget = appConfig['chunkGbTarget']

    sourceClient = pymongo.MongoClient(host=appConfig['sourceUri'])

    targetClient = pymongo.MongoClient(host=appConfig['targetUri'])
    targetDb = targetClient[appConfig['mm3kDatabase']]
    targetColl = targetDb['collections']
    statusColl = targetDb['status']

    allDone = False
    numNoDocuments = 0

    # loop through catalogged collections
    while not allDone:
        thisCollection = targetColl.find_one_and_update({'status':'CATALOGGED'},{'$set':{'status':'INSPECTING'}})
        if thisCollection == None:
            # wait and try again
            if appConfig['verboseLogging']:
                logIt(logName,logId,'no work found # {}'.format(numNoDocuments))
            numNoDocuments += 1
            if numNoDocuments >= 6:
                allDone = True
            else:
                time.sleep(5)
            continue
        logIt(logName,logId,'inspecting {}.{}'.format(thisCollection['database'],thisCollection['collection']))
        numNoDocuments = 0

        db = sourceClient[thisCollection['database']]
        collStats = db.command("collStats",thisCollection['collection'])
        numDocuments = collStats['count']
        avgObjSize = int(collStats['avgObjSize'])
        rowsPerChunk = int(chunkGbTarget * 1024 * 1024 * 1024 / avgObjSize)
        size = collStats['size']
        storageSize = collStats['storageSize']

        targetColl.update_one({'_id':thisCollection['_id']},
                              {'$set':{'status':'INSPECTED',
                                       'numDocuments':numDocuments,
                                       'avgObjSize':avgObjSize,
                                       'rowsPerChunk':rowsPerChunk,
                                       'size':size,
                                       'storageSize':storageSize}})

        statusColl.update_one({'_id':1},{'$inc':{'totalDocuments':numDocuments,'totalBytes':size}})
        
    sourceClient.close()
    targetClient.close()


def segmenter(appConfig,threadNum):
    # catalog the effort - namespaces and their document count, average document size, size on disk
    warnings.filterwarnings("ignore","You appear to be connected to a DocumentDB cluster.")

    logName = 'SEGMENTER'
    logId = threadNum 

    sourceClient = pymongo.MongoClient(host=appConfig['sourceUri'])

    targetClient = pymongo.MongoClient(host=appConfig['targetUri'])
    targetDb = targetClient[appConfig['mm3kDatabase']]
    targetColl = targetDb['collections']
    targetCollSegments = targetDb['segments']
    statusColl = targetDb['status']

    allDone = False
    numNoDocuments = 0

    # loop through inspected collections
    while not allDone:
        thisCollection = targetColl.find_one_and_update({'status':'INSPECTED'},{'$set':{'status':'SEGMENTING'}})
        startTime = time.time()
        if thisCollection == None:
            # wait and try again
            if appConfig['verboseLogging']:
                logIt(logName,logId,'no work found # {}'.format(numNoDocuments))
            numNoDocuments += 1
            if numNoDocuments >= 5:
                allDone = True
            else:
                time.sleep(6)
            continue
        logIt(logName,logId,'segmenting {}.{}'.format(thisCollection['database'],thisCollection['collection']))
        numNoDocuments = 0

        # we have a collection to segment
        numSegments = segmentCollection(appConfig,thisCollection,sourceClient,targetDb,threadNum)
        endTime = time.time()

        targetColl.update_one({'_id':thisCollection['_id']},
                              {'$set':{'status':'SEGMENTED',
                                       'numSegments':numSegments,
                                       'segmentStartTime':dt.datetime.fromtimestamp(startTime,tz=dt.timezone.utc),
                                       'segmentEndTime':dt.datetime.fromtimestamp(endTime,tz=dt.timezone.utc),
                                       'segmentSeconds':endTime-startTime}})

        
    sourceClient.close()
    targetClient.close()


def segmentCollection(appConfig,thisCollection,sourceClient,targetDb,threadNum):
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

    logIt(logName,logId,"collection {}.{} contains {} documents".format(sourceDb,sourceColl,numDocuments))
    logIt(logName,logId,"calculated {} documents for a {} GB chunk of {} average object (bytes)".format(rowsPerChunk,chunkGbTarget,avgObjSize))

    allDone = False

    queryStartTime = time.time()

    # get the first _id
    minId = col.find_one(filter=None,projection={"_id":True},sort=[("_id",pymongo.ASCENDING)])
    maxId = col.find_one(filter=None,projection={"_id":True},sort=[("_id",pymongo.DESCENDING)])
    currentId = minId
    priorId = minId

    numDocsTotal = 0
    numBoundaries = 0

    while not allDone:
        startTime = time.time()
        currentId = col.find_one(filter={"_id":{"$gt":currentId["_id"]}},projection={"_id":True},sort=[("_id",pymongo.ASCENDING)],skip=rowsPerChunk)
        endTime = time.time()
        numSeconds = endTime - startTime

        # no more boundaries
        if currentId is None:
            # create final segment
            if numBoundaries == 0:
                # single segment collection
                result = targetColl.insert_one({'database':sourceDb,'collection':sourceColl,'segment':1,'minId':minId['_id'],'maxId':maxId['_id'],'segmentStartTime':dt.datetime.fromtimestamp(startTime,tz=dt.timezone.utc),'segmentEndTime':dt.datetime.fromtimestamp(endTime,tz=dt.timezone.utc),'segmentSeconds':numSeconds,'status':'SEGMENTED'})
            else:
                # multiple segment collection
                result = targetColl.insert_one({'database':sourceDb,'collection':sourceColl,'segment':numBoundaries+1,'minId':priorId['_id'],'maxId':maxId['_id'],'segmentStartTime':dt.datetime.fromtimestamp(startTime,tz=dt.timezone.utc),'segmentEndTime':dt.datetime.fromtimestamp(endTime,tz=dt.timezone.utc),'segmentSeconds':numSeconds,'status':'SEGMENTED'})

            result = statusColl.update_one({'_id':1},{'$inc':{'totalSegments':1}})
            allDone = True
            continue
        else:
            # create segment
            result = targetColl.insert_one({'database':sourceDb,'collection':sourceColl,'segment':numBoundaries+1,'minId':priorId['_id'],'maxId':currentId['_id'],'segmentStartTime':dt.datetime.fromtimestamp(startTime,tz=dt.timezone.utc),'segmentEndTime':dt.datetime.fromtimestamp(endTime,tz=dt.timezone.utc),'segmentSeconds':numSeconds,'status':'SEGMENTED'})
            result = statusColl.update_one({'_id':1},{'$inc':{'totalSegments':1}})

        priorId = currentId
        numDocsTotal += rowsPerChunk
        pctDone = numDocsTotal/(numDocuments - rowsPerChunk)*100
        elapsedSecs = int(time.time() - queryStartTime)
        estimatedSecsToDone = max(0,int(((100/pctDone)*elapsedSecs)-elapsedSecs))
        numBoundaries += 1
        logIt(logName,logId,"ns {}.{} | boundary {:3d} - {} {} | done in approximately {} seconds".format(sourceDb,sourceColl,numBoundaries,type(currentId["_id"]),currentId["_id"],estimatedSecsToDone))
        #boundaryList.append(currentId["_id"])

    return numBoundaries+1


def loader(processNum, appConfig):
    # load segments
    warnings.filterwarnings("ignore","You appear to be connected to a DocumentDB cluster.")

    logName = 'LOADER'
    logId = processNum

    targetClient = pymongo.MongoClient(host=appConfig['targetUri'])
    targetDb = targetClient[appConfig['mm3kDatabase']]
    targetColl = targetDb['collections']
    targetCollSegments = targetDb['segments']
    statusColl = targetDb['status']

    dryRun = appConfig['dryRun']

    sourceClient = pymongo.MongoClient(host=appConfig['sourceUri'])

    allDone = False
    numNoDocuments = 0

    # loop through inspected collections
    while not allDone:
        thisSegment = targetCollSegments.find_one_and_update({'status':'SEGMENTED'},{'$set':{'status':'LOADING'}})
        startTime = time.time()
        if thisSegment == None:
            # wait and try again
            if appConfig['verboseLogging']:
                logIt(logName,logId,'no work found # {}'.format(numNoDocuments))
            numNoDocuments += 1
            if numNoDocuments >= 6:
                allDone = True
            else:
                time.sleep(5)
            continue

        logIt(logName,logId,'loading segment {} of {}.{}'.format(thisSegment['segment'],thisSegment['database'],thisSegment['collection']))

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

    startTime = time.time()
    lastFeedback = time.time()

    bulkOpList = []

    numCurrentBulkOps = 0
    numTotalBytes = 0
    numTotalBatches = 0
    numTotalInserts = 0

    boundaryFieldName = '_id'

    # special query for first segment, need to include minId
    if thisSegment['segment'] == 1:
        cursor = sourceColl.find({boundaryFieldName: {'$gte': thisSegment['minId'], '$lte': thisSegment['maxId']}})
    else:
        cursor = sourceColl.find({boundaryFieldName: {'$gt': thisSegment['minId'], '$lte': thisSegment['maxId']}})

    for doc in cursor:
        numTotalInserts += 1
        numTotalBytes += len(encode(doc))
        numCurrentBulkOps += 1
        bulkOpList.append(pymongo.InsertOne(doc))

        if (numCurrentBulkOps >= appConfig["maxInsertsPerBatch"]):
            if not dryRun:
                result = targetColl.bulk_write(bulkOpList,ordered=False)
            bulkOpList = []
            numCurrentBulkOps = 0
            numTotalBatches += 1

    if (numCurrentBulkOps > 0):
        if not dryRun:
            result = targetColl.bulk_write(bulkOpList,ordered=False)
        bulkOpList = []
        numCurrentBulkOps = 0
        numTotalBatches += 1

    return numTotalInserts, numTotalBytes


def main():
    parser = argparse.ArgumentParser(description='Mongo migrator 3000')

    parser.add_argument('--source-uri',required=True,type=str,help='Source URI')
    parser.add_argument('--target-uri',required=True,type=str,help='Target URI')
    parser.add_argument('--verbose',required=False,action='store_true',help='Enable verbose logging')
    parser.add_argument('--num-full-load-workers',required=False,default=10,type=int,help='Number of workers performing full load')
    parser.add_argument('--chunk-gb-target',required=False,default=1,type=int,help='Target/maximum GB for each full load chunk')
    parser.add_argument('--mm3k-database',required=False,type=str,default='mm3k-state',help='Source URI')

    parser.add_argument('--num-segmenters',required=False,type=int,default=10,help='Maximum number of concurrent segmenters')
    parser.add_argument('--num-loaders',required=False,type=int,default=10,help='Maxiumum number of concurrent loadersd')
                        
    #parser.add_argument('--source-namespace',required=True,type=str,help='Source Namespace as <database>.<collection>')
    #parser.add_argument('--target-namespace',required=False,type=str,help='Target Namespace as <database>.<collection>, defaults to --source-namespace')
    #parser.add_argument('--feedback-seconds',required=False,type=int,default=60,help='Number of seconds between feedback output')
    parser.add_argument('--max-inserts-per-batch',required=False,type=int,default=100,help='Maximum number of inserts to include in a single batch')
    parser.add_argument('--dry-run',required=False,action='store_true',help='Read only, do not apply to target (except --mm3k-database')
    #parser.add_argument('--create-cloudwatch-metrics',required=False,action='store_true',help='Create CloudWatch metrics')
    #parser.add_argument('--cluster-name',required=False,type=str,help='Name of cluster for CloudWatch metrics')

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
    appConfig['chunkGbTarget'] = int(args.chunk_gb_target)
    appConfig['mm3kDatabase'] = args.mm3k_database
    appConfig['numSegmenters'] = args.num_segmenters
    appConfig['numLoaders'] = args.num_loaders
    appConfig['maxInsertsPerBatch'] = args.max_inserts_per_batch
    #appConfig['feedbackSeconds'] = args.feedback_seconds
    appConfig['dryRun'] = args.dry_run
    #appConfig['createCloudwatchMetrics'] = args.create_cloudwatch_metrics
    #appConfig['clusterName'] = args.cluster_name

    mp.set_start_method('spawn')
    #q = mp.Manager().Queue()

    tCoordinator = threading.Thread(target=coordinator,args=(appConfig,))
    tCoordinator.start()

    tCatalogger = threading.Thread(target=catalogger,args=(appConfig,))
    tCatalogger.start()

    tInspector = threading.Thread(target=inspector,args=(appConfig,))
    tInspector.start()

    segmenterList = []
    for loop in range(appConfig['numSegmenters']):
        tSegmenter = threading.Thread(target=segmenter,args=(appConfig,loop,))
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


if __name__ == "__main__":
    main()
