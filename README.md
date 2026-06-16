# mongo-migrator-3000
Start to finish data migration for MongoDB API compatible databases

# the goal
Easy, performant, and correct data migrations between document databases
- no long running queries
- fully resumable (via graceful stop or crash)
- highly observable
- zero configuration required

# the plan
prep
- [ ] assess source
- [x] persist collection information
- [ ] support single collection, single database, everything
- [ ] exception by namespace

data load (DL)
- [ ] must support resume, use replace() for resuming chunks
- [x] collection chunker(s), persist work into queue collection - hi/low
- [x] drive FL from chunks
- [ ] dynamically increase/decrease concurrency and throughput

change data capture (CDC)
- [ ] readahead for buffercache warm up
- [ ] 1:1 read/write CDC
- [ ] 1:n read/write CDC
- [ ] updates as updates, no fullDocumentLookup

observability
- all via persistence in target
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
- [ ] if 1:1 read/write CDC, server side hashing/filtering
- [ ] begin CDC for completed collection FL
- [ ] test performance of transactionally wrapped CDC bulk write operations
- [ ] filtering by expression

questions
- Q: support unique secondary indexes?
- Q: how to handle unique _id or secondary index violations when not resuming?
- Q: support transactions?
- Q: support DDL?

