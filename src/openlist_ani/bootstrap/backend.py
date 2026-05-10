"""
Backend process entry point.

Orchestrates the full application lifecycle:
- Database initialization
- Downloader, event manager, memento store
- Pipeline runtime
- FastAPI API server (uvicorn)
"""

import asyncio
import sys

import uvicorn

from openlist_ani.adapters.inbound.http.app import create_app
from openlist_ani.adapters.inbound.http.service import BackendApiService
from openlist_ani.adapters.outbound.configuration import ConfigValidator, config
from openlist_ani.adapters.outbound.downloaders import (
    DownloaderRegistry,
    OpenListDownloader,
)
from openlist_ani.adapters.outbound.events import OAniEventManager
from openlist_ani.adapters.outbound.feed_sources import (
    FeedSourceFactory,
    ReleaseFeedReader,
)
from openlist_ani.adapters.outbound.file_renamers import (
    FileRenamerRegistry,
    OpenListFileRenamer,
)
from openlist_ani.adapters.outbound.metadata_parser import (
    MetadataParserAdapter,
    MetadataParserRegistry,
    MetadataParserSettings,
)
from openlist_ani.adapters.outbound.notifications import (
    NotificationManager,
    NotificationManagerFactory,
    NotificationBotSettings,
    NotificationSettings,
)
from openlist_ani.adapters.outbound.persistence import (
    JsonTaskMementoStore,
    SqliteAnimeLibraryRepository,
)
from openlist_ani.adapters.outbound.torrent_metadata import (
    resolve_magnet,
    resolve_torrent,
)
from openlist_ani.application.anime_library_ingestion import (
    AnimeLibraryIngestionPipeline,
)
from openlist_ani.application.anime_library_ingestion.settings import (
    AnimeLibraryIngestionSettings,
    MetadataFilterSettings,
    PrioritySettings,
)
from openlist_ani.application.anime_library_ingestion.application_service import (
    AnimeLibraryApplicationService,
)
from openlist_ani.integrations.openlist import OpenListClient, OpenListHealthCheck
from openlist_ani.logger import FATAL_LEVEL, configure_logger, logger

startup_logger = logger
api_logger = logger


async def run() -> None:
    """Start the backend process: API server + background workers."""
    configure_logger(
        level=config.log.level,
        rotation=config.log.rotation,
        retention=config.log.retention,
        log_name="openlist_ani",
    )

    if not ConfigValidator(config.data, config.load_failed).validate():
        startup_logger.log(FATAL_LEVEL, "Configuration validation failed; exiting")
        sys.exit(1)

    openlist_client = _create_openlist_client()
    if not await _validate_openlist(openlist_client):
        startup_logger.log(FATAL_LEVEL, "OpenList validation failed; exiting")
        await openlist_client.close()
        sys.exit(1)

    _log_startup_summary()

    anime_library_repository = SqliteAnimeLibraryRepository()
    await anime_library_repository.init()

    event_manager = OAniEventManager()
    await event_manager.start()

    notification_manager = await _setup_notifications()
    task_memento_store = JsonTaskMementoStore("data/task_mementos.json")
    settings = _create_pipeline_settings()
    metadata_parser = _create_metadata_parser()
    downloader = _create_downloader(openlist_client)
    file_renamer = _create_file_renamer(openlist_client)
    pipeline = AnimeLibraryIngestionPipeline(
        downloader=downloader,
        file_renamer=file_renamer,
        task_store=task_memento_store,
        event_publisher=event_manager,
        anime_library_repository=anime_library_repository,
        metadata_parser=metadata_parser,
        settings=settings,
        feed_reader=ReleaseFeedReader(list(config.rss.urls)),
        notifier=notification_manager,
    )
    await pipeline.start()
    BackendApiService.init(
        AnimeLibraryApplicationService(
            pipeline=pipeline,
            metadata_parser=metadata_parser,
            anime_library_repository=anime_library_repository,
            settings=settings,
            feed_factory=FeedSourceFactory(),
            resolve_magnet_func=resolve_magnet,
            resolve_torrent_func=resolve_torrent,
            get_rss_urls=lambda: list(config.rss.urls),
            add_rss_url_func=config.add_rss_url,
        )
    )

    server = _create_api_server()
    api_task = asyncio.create_task(server.serve())
    api_logger.info(
        f"Backend API server listening on {config.backend.host}:{config.backend.port}"
    )

    try:
        await api_task
    except asyncio.CancelledError:
        startup_logger.info("Shutting down...")
        server.should_exit = True
        await asyncio.gather(api_task, return_exceptions=True)
        raise
    except KeyboardInterrupt:
        startup_logger.info("Interrupted by user.")
    finally:
        await pipeline.stop()
        task_memento_store.atomic_flush()
        await event_manager.stop()
        await openlist_client.close()
        try:
            await metadata_parser.close()
        except Exception as e:
            startup_logger.warning(f"Failed to close metadata parser cleanly: {e}")
        if notification_manager:
            await notification_manager.stop()


def _log_startup_summary() -> None:
    startup_logger.info("=" * 56)
    startup_logger.info("OpenList-Ani starting")
    startup_logger.info(f"RSS sources   : {len(config.rss.urls)} configured")
    startup_logger.info(f"Download path : {config.openlist.download_path}")
    startup_logger.info(f"LLM model     : {config.llm.openai_model}")
    startup_logger.info(f"OpenList URL  : {config.openlist.url}")
    startup_logger.info(f"Backend API   : {config.backend.host}:{config.backend.port}")
    startup_logger.info("=" * 56)


def _create_openlist_client() -> OpenListClient:
    return OpenListClient(base_url=config.openlist.url, token=config.openlist.token)


async def _validate_openlist(client: OpenListClient) -> bool:
    return await OpenListHealthCheck(
        client=client,
        base_url=config.openlist.url,
        offline_download_tool=config.openlist.offline_download_tool,
    ).validate()


def _create_downloader(openlist_client: OpenListClient):
    registry = DownloaderRegistry()
    registry.register(
        "openlist",
        lambda: OpenListDownloader(
            client=openlist_client,
            offline_download_tool=config.openlist.offline_download_tool,
        ),
    )
    return registry.create(config.downloader.provider)


def _create_file_renamer(openlist_client: OpenListClient):
    registry = FileRenamerRegistry()
    registry.register("openlist", lambda: OpenListFileRenamer(openlist_client))
    return registry.create(config.file_renamer.provider)


def _create_metadata_parser():
    registry = MetadataParserRegistry()
    registry.register(
        "llm_tmdb",
        lambda: MetadataParserAdapter.from_settings(
            MetadataParserSettings(
                provider_type=config.llm.provider_type,
                api_key=config.llm.openai_api_key,
                base_url=config.llm.openai_base_url,
                model=config.llm.openai_model,
                tmdb_api_key=config.llm.tmdb_api_key,
                tmdb_language=config.llm.tmdb_language,
            )
        ),
    )
    return registry.create(config.metadata_parser.provider)


async def _setup_notifications() -> NotificationManager | None:
    """Start notification manager if configured."""
    notification_manager = NotificationManagerFactory().create(
        NotificationSettings(
            enabled=config.notification.enabled,
            batch_interval=config.notification.batch_interval,
            bots=[
                NotificationBotSettings(
                    type=bot.type,
                    enabled=bot.enabled,
                    config=dict(bot.config),
                )
                for bot in config.notification.bots
            ],
        )
    )
    if not notification_manager:
        return None

    await notification_manager.start()
    return notification_manager


def _create_pipeline_settings() -> AnimeLibraryIngestionSettings:
    return AnimeLibraryIngestionSettings(
        download_path=config.openlist.download_path,
        rename_format=config.openlist.rename_format,
        rss_interval_seconds=config.rss.interval_time,
        strict_filtering=config.rss.strict,
        metadata_filter=MetadataFilterSettings(
            exclude_fansub=list(config.rss.filter.exclude_fansub),
            exclude_quality=list(config.rss.filter.exclude_quality),
            exclude_languages=list(config.rss.filter.exclude_languages),
            exclude_patterns=list(config.rss.filter.exclude_patterns),
        ),
        priority=PrioritySettings(
            field_order=list(config.rss.priority.field_order),
            fansub=list(config.rss.priority.fansub),
            languages=list(config.rss.priority.languages),
            quality=list(config.rss.priority.quality),
        ),
    )


def _create_api_server() -> uvicorn.Server:
    """Create the uvicorn server instance for the FastAPI app."""
    fastapi_app = create_app()
    uvicorn_config = uvicorn.Config(
        fastapi_app,
        host=config.backend.host,
        port=config.backend.port,
        log_level="warning",
    )
    return uvicorn.Server(uvicorn_config)


def main() -> None:
    """Synchronous CLI entry point for the backend process."""
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
