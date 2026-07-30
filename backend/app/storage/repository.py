"""Small SQLAlchemy repository used by the readiness boundary."""

from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class DatabaseHealthRepository:
    """Keep database access out of the HTTP handler."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def is_available(self) -> bool:
        async with self._session_factory() as session:
            result = await session.execute(text("SELECT 1"))
            return bool(result.scalar_one() == 1)

    async def schema_revision(self) -> str | None:
        try:
            async with self._session_factory() as session:
                result = await session.execute(text("SELECT version_num FROM alembic_version"))
                return result.scalar_one_or_none()
        except OperationalError:
            return None
