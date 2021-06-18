import argparse
import asyncio
import logging
from elasticsearch import AsyncElasticsearch
from elasticsearch.helpers import async_bulk
from lbry.wallet.server.env import Env
from lbry.wallet.server.coin import LBC
from lbry.wallet.server.leveldb import LevelDB
from lbry.wallet.server.db.elasticsearch.search import extract_doc, SearchIndex, IndexVersionMismatch


async def get_all_claims(index_name='claims', db=None):
    env = Env(LBC)
    need_open = db is None
    db = db or LevelDB(env)
    if need_open:
        await db.open_dbs()
    try:
        cnt = 0
        for claim in db.all_claims_producer():
            yield extract_doc(claim, index_name)
            cnt += 1
            if cnt % 10000 == 0:
                print(f"{cnt} claims sent")
    finally:
        if need_open:
            db.close()


async def make_es_index(index=None):
    env = Env(LBC)
    if index is None:
        index = SearchIndex('', elastic_host=env.elastic_host, elastic_port=env.elastic_port)

    try:
        return await index.start()
    except IndexVersionMismatch as err:
        logging.info(
            "dropping ES search index (version %s) for upgrade to version %s", err.got_version, err.expected_version
        )
        await index.delete_index()
        await index.stop()
        return await index.start()
    finally:
        index.stop()


async def run_sync(index_name='claims', db=None):
    env = Env(LBC)
    logging.info("ES sync host: %s:%i", env.elastic_host, env.elastic_port)
    es = AsyncElasticsearch([{'host': env.elastic_host, 'port': env.elastic_port}])
    try:
        await async_bulk(es, get_all_claims(index_name=index_name, db=db), request_timeout=120)
        await es.indices.refresh(index=index_name)
    finally:
        await es.close()


def __run(args, shard):
    asyncio.run(run_sync())


def run_elastic_sync():
    logging.basicConfig(level=logging.INFO)
    logging.getLogger('aiohttp').setLevel(logging.WARNING)
    logging.getLogger('elasticsearch').setLevel(logging.WARNING)

    logging.info('lbry.server starting')
    parser = argparse.ArgumentParser(prog="lbry-hub-elastic-sync")
    # parser.add_argument("db_path", type=str)
    parser.add_argument("-c", "--clients", type=int, default=16)
    parser.add_argument("-b", "--blocks", type=int, default=0)
    parser.add_argument("-f", "--force", default=False, action='store_true')
    args = parser.parse_args()

    # if not args.force and not os.path.exists(args.db_path):
    #     logging.info("DB path doesnt exist")
    #     return

    if not args.force and not asyncio.run(make_es_index()):
        logging.info("ES is already initialized")
        return
    asyncio.run(run_sync())
