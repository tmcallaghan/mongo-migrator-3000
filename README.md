# mongo-migrator-3000
Start to finish data migration for MongoDB API compatible databases

# the goal
Easy, performant, and correct data migrations between document databases

# the plan
prep
- [ ] assess source
- [ ] persist collection information

data load (DL)
- [ ] must support resume, use replace() for resuming chunks
- [ ] collection chunker, persist
- [ ] drive DL from chunks

change data capture (CDC)
- [ ] readahead for buffercache warm up
- [ ] 1:1 read/write CDC
- [ ] 1:n read/write CDC

observability
- all via persistence
- [ ] tui
- [ ] web page
- [ ] aws cloudwatch
- [ ] usable logging of errors/issues/exceptions

long-term goals
- [ ] monitor source churn rate
- [ ] command and control
- [ ] indexes
- [ ] users
- [ ] role based access control (RBAC)
- [ ] begin gather of CDC immediately, use S3 or other
- [ ] chunked CDC gather
- [ ] if using zstd/dict in AWS DocumentDB, load ~1000 and await dictionary
- [ ] dynamic scaling of read/write DL concurrency
- [ ] if 1:1 read/write CDC, server side hashing/filtering
- [ ] begin CDC for completed collection DL
- [ ] test performance of transactionally wrapped CDC bulk write operations

questions
- Q: support unique secondary indexes?
- Q: how to handle unique _id or secondary index violations when not resuming?
- Q: support transactions?
- Q: support DDL?

DL schema/algorithm
- capture all participating collections (namespaces) [collectionsMigrating]
	- db, coll, doc count, size, storageSize, avg doc (as reported), avg doc (sampled)
	- decide if parallel or not - check min/max _id, min size
		- if not, single document into collectionChunks, sets parallel:false, status:"ReadyForFullLoad"
- for each namespace going parallel [collectionChunks]
	- chunker grabs document from collectionsMigrating, sets parallel:true, status:"Chunking"
	- creates one document per chunk into collectionChunks
		- db, coll, startTime, endTime, minId, maxId, actualDocuments
- for each document in collectionChunks
	- set status:"FullLoad", set startTime
	- insert from minId to maxId (or no minId/maxId if not set)
		- small collections, mixed _id types