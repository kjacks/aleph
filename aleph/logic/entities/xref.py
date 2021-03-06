import logging
from pprint import pprint  # noqa
from followthemoney import model
from followthemoney.types import registry
from followthemoney.compare import compare

from aleph.core import db, es, celery
from aleph.model import Match
from aleph.index.indexes import entities_read_index
from aleph.index.entities import iter_proxies, entities_by_ids
from aleph.logic.entities.match import match_query
from aleph.index.util import unpack_result, none_query
from aleph.index.util import BULK_PAGE
from aleph.index.collections import get_collection
from aleph.logic.util import entity_url

log = logging.getLogger(__name__)
SCORE_CUTOFF = 0.05


def xref_item(proxy, collection_ids=None):
    """Cross-reference an entity or document, given as an indexed document."""
    query = match_query(proxy, collection_ids=collection_ids)
    if query == none_query():
        return

    query = {
        'query': query,
        'size': 100,
        '_source': {'includes': ['schema', 'properties', 'collection_id']}
    }
    matchable = list(proxy.schema.matchable_schemata)
    index = entities_read_index(schema=matchable)
    result = es.search(index=index, body=query)
    results = result.get('hits').get('hits')
    for result in results:
        result = unpack_result(result)
        if result is not None:
            other = model.get_proxy(result)
            score = compare(model, proxy, other)
            if score >= SCORE_CUTOFF:
                yield score, result.get('collection_id'), other


@celery.task()
def xref_collection(collection_id, against_collection_ids=None):
    """Cross-reference all the entities and documents in a collection."""
    matchable = [s.name for s in model if s.matchable]
    entities = iter_proxies(collection_id=collection_id, schemata=matchable)
    for entity in entities:
        proxy = model.get_proxy(entity)
        dq = db.session.query(Match)
        dq = dq.filter(Match.entity_id == proxy.id)
        dq.delete()
        matches = xref_item(proxy, collection_ids=against_collection_ids)
        for (score, other_id, other) in matches:
            log.info("Xref [%.3f]: %s <=> %s", score, proxy, other)
            obj = Match()
            obj.entity_id = proxy.id
            obj.collection_id = collection_id
            obj.match_id = other.id
            obj.match_collection_id = other_id
            obj.score = score
            db.session.add(obj)
        db.session.commit()


def _format_date(proxy):
    dates = proxy.get_type_values(registry.date)
    if not len(dates):
        return ''
    return min(dates)


def _format_country(proxy):
    countries = [c.upper() for c in proxy.countries]
    return ', '.join(countries)


def _iter_match_batch(batch, authz):
    matchable = [s.name for s in model if s.matchable]
    entities = set()
    for match in batch:
        entities.add(match.entity_id)
        entities.add(match.match_id)

    entities = entities_by_ids(list(entities), schemata=matchable)
    entities = {e.get('id'): e for e in entities}
    for obj in batch:
        if not authz.can(obj.match_collection_id, authz.READ):
            continue
        entity = entities.get(str(obj.entity_id))
        match = entities.get(str(obj.match_id))
        collection = get_collection(obj.match_collection_id)
        if entity is None or match is None or collection is None:
            continue
        eproxy = model.get_proxy(entity)
        mproxy = model.get_proxy(match)
        yield (
            int(obj.score * 100),
            eproxy.caption,
            _format_date(eproxy),
            _format_country(eproxy),
            collection.get('label'),
            mproxy.caption,
            _format_date(mproxy),
            _format_country(mproxy),
            entity_url(eproxy.id),
            entity_url(mproxy.id),
        )


def export_matches_csv(collection_id, authz):
    """Export the top N matches of cross-referencing for the given collection
    to an Excel 2010 formatted export."""
    dq = db.session.query(Match)
    dq = dq.filter(Match.collection_id == collection_id)
    dq = dq.order_by(Match.score.desc())
    yield [
        'Score',
        'EntityName',
        'EntityDate',
        'EntityCountries',
        'MatchCollection',
        'MatchName',
        'MatchDate',
        'MatchCountries',
        'EntityLink',
        'MatchLink',
    ]
    batch = []
    for match in dq.yield_per(BULK_PAGE):
        batch.append(match)
        if len(batch) >= BULK_PAGE:
            yield from _iter_match_batch(batch, authz)
            batch = []
    if len(batch):
        yield from _iter_match_batch(batch, authz)
