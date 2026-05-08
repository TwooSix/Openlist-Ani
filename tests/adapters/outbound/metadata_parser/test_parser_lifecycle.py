import asyncio

from openlist_ani.adapters.outbound.metadata_parser.parser import MetadataParserAdapter


class FakeTMDBResolver:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True
        await asyncio.sleep(0)


async def test_metadata_parser_adapter_closes_tmdb_resolver():
    resolver = FakeTMDBResolver()
    adapter = MetadataParserAdapter(
        llm_client=None,
        tmdb_resolver=resolver,
    )

    await adapter.close()

    assert resolver.closed is True
