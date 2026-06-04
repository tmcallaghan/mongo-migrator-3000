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


def logIt(threadnum, message):
    logTimeStamp = dt.datetime.now(dt.timezone.utc).isoformat()[:-3] + 'Z'
    print("[{}] thread {:>3d} | {}".format(logTimeStamp,threadnum,message))


'''
def getCollectionCount(appConfig):
    warnings.filterwarnings("ignore","You appear to be connected to a DocumentDB cluster.")

    sourceDb = appConfig["sourceNs"].split('.',1)[0]
    sourceColl = appConfig["sourceNs"].split('.',1)[1]
    client = pymongo.MongoClient(appConfig['sourceUri'])
    db = client[sourceDb]
    collStats = db.command("collStats", sourceColl)
    client.close()
    return max(collStats['count'],1)
'''


def segmenter(appConfig):
    # get boundaries by performing server-side skips
    warnings.filterwarnings("ignore","You appear to be connected to a DocumentDB cluster.")

    sourceDb = appConfig["sourceNs"].split('.',1)[0]
    sourceColl = appConfig["sourceNs"].split('.',1)[1]
    client = pymongo.MongoClient(host=appConfig['sourceUri'])
    db = client[sourceDb]
    col = db[sourceColl]

    chunkGbTarget = appConfig['chunkGbTarget']

    collStats = db.command("collStats",sourceColl)
    numDocuments = collStats['count']
    avgObjSize = int(collStats['avgObjSize'])

    rowsPerChunk = int(chunkGbTarget * 1024 * 1024 * 1024 / avgObjSize)

    logIt(0,"{}".format(collStats))

    logIt(0,"")
    logIt(0,"+ collection {}.{} contains {} documents".format(sourceDb,sourceColl,numDocuments))
    logIt(0,"+ calculated {} documents for a {} GB chunk of {} average object (bytes)".format(rowsPerChunk,chunkGbTarget,avgObjSize))

    allDone = False

    queryStartTime = time.time()

    # get the first _id
    currentId = col.find_one(filter=None,projection={"_id":True},sort=[("_id",pymongo.ASCENDING)])
    print("  found first _id")
    numDocsTotal = 0

    while not allDone:
        currentId = col.find_one(filter={"_id":{"$gt":currentId["_id"]}},projection={"_id":True},sort=[("_id",pymongo.ASCENDING)],skip=rowsPerChunk)
        print("{}".format(currentId))
        numDocsTotal += rowsPerChunk
        pctDone = numDocsTotal/(numDocuments - rowsPerChunk)*100
        elapsedSecs = int(time.time() - queryStartTime)
        estimatedSecsToDone = int(((100/pctDone)*elapsedSecs)-elapsedSecs)
        logIt(0,"  boundary {:3d} - {} {} | done in approximately {} seconds".format(x+1,type(currentId["_id"]),currentId["_id"],estimatedSecsToDone))
        #boundaryList.append(currentId["_id"])


    '''
    for x in range(numBoundaries):
        currentId = col.find_one(filter={"_id":{"$gt":currentId["_id"]}},projection={"_id":True},sort=[("_id",pymongo.ASCENDING)],skip=feedbackDocuments)
        numDocsTotal += feedbackDocuments
        pctDone = numDocsTotal/(numDocuments - feedbackDocuments)*100
        elapsedSecs = int(time.time() - queryStartTime)
        estimatedSecsToDone = int(((100/pctDone)*elapsedSecs)-elapsedSecs)
        print("  boundary {:3d} - {} {} | done in approximately {} seconds".format(x+1,type(currentId["_id"]),currentId["_id"],estimatedSecsToDone))
        boundaryList.append(currentId["_id"])

    boundaryListAsString = "{}".format(",".join('"{}"'.format(i) for i in boundaryList))
    print("")
    print("boundaries as list | {}".format(boundaryListAsString))

    boundaryListAsStringForDms = "[{}]".format("],[".join('"{}"'.format(i) for i in boundaryList))
    print("")
    print("boundaries as list for DMS | {}".format(boundaryListAsStringForDms))

    print("")

    queryElapsedSecs = int(time.time() - queryStartTime)
    print('query required {} seconds'.format(queryElapsedSecs))

    print("")
    '''
        
    client.close()


def main():
    parser = argparse.ArgumentParser(description='Mongo migrator 3000')

    parser.add_argument('--source-uri',required=True,type=str,help='Source URI')
    parser.add_argument('--target-uri',required=True,type=str,help='Target URI')
    parser.add_argument('--source-namespace',required=True,type=str,help='Source Namespace as <database>.<collection>')
    parser.add_argument('--target-namespace',required=False,type=str,help='Target Namespace as <database>.<collection>, defaults to --source-namespace')
    parser.add_argument('--verbose',required=False,action='store_true',help='Enable verbose logging')
    parser.add_argument('--num-full-load-workers',required=False,default=10,type=int,help='Number of workers performing full load')
    parser.add_argument('--chunk-gb-target',required=False,default=1,type=int,help='Target/maximum GB for each full load chunk')
                        
    #parser.add_argument('--feedback-seconds',required=False,type=int,default=60,help='Number of seconds between feedback output')
    #parser.add_argument('--max-inserts-per-batch',required=False,type=int,default=100,help='Maximum number of inserts to include in a single batch')
    #parser.add_argument('--dry-run',required=False,action='store_true',help='Read source changes only, do not apply to target')
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
    appConfig['sourceNs'] = args.source_namespace
    if not args.target_namespace:
        appConfig['targetNs'] = args.source_namespace
    else:
        appConfig['targetNs'] = args.target_namespace
    appConfig['verboseLogging'] = args.verbose
    appConfig['numFullLoadWorkers'] = int(args.num_full_load_workers)
    appConfig['chunkGbTarget'] = int(args.chunk_gb_target)

    #appConfig['maxInsertsPerBatch'] = args.max_inserts_per_batch
    #appConfig['feedbackSeconds'] = args.feedback_seconds
    #appConfig['dryRun'] = args.dry_run
    #appConfig['boundaryFieldName'] = args.boundary_field_name
    #appConfig['boundaryDatatype'] = args.boundary_datatype
    #appConfig['createCloudwatchMetrics'] = args.create_cloudwatch_metrics
    #appConfig['clusterName'] = args.cluster_name

    #boundaryList = args.boundaries.split(',')
    #appConfig['boundaries'] = []
    #for thisBoundary in boundaryList:
    #    if appConfig['boundaryDatatype'] == 'objectid':
    #        appConfig['boundaries'].append(ObjectId(thisBoundary))
    #    elif appConfig['boundaryDatatype'] == 'string':
    #        appConfig['boundaries'].append(thisBoundary)
    #    else:
    #        appConfig['boundaries'].append(int(thisBoundary))

    #appConfig['numDocumentsToMigrate'] = getCollectionCount(appConfig)
    
    logIt(-1,"full load using {} workers".format(appConfig['numFullLoadWorkers']))

    mp.set_start_method('spawn')
    q = mp.Manager().Queue()
    #tController = threading.Thread(target=segmenter,args=(appConfig,q))

    tController = threading.Thread(target=segmenter,args=(appConfig,))
    tController.start()

    #t = threading.Thread(target=reporter,args=(appConfig,q))
    #t.start()
    
    #processList = []
    #for loop in range(appConfig["numProcessingThreads"]):
    #    p = mp.Process(target=full_load_loader,args=(loop,appConfig,q))
    #    processList.append(p)
    #    
    #for process in processList:
    #    process.start()
    #    
    #for process in processList:
    #    process.join()
        
    tController.join()


if __name__ == "__main__":
    main()
