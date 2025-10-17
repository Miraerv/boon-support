# db.py (fixed with shared engine and proper connection pooling)

import datetime
import uuid
from dataclasses import asdict, dataclass
from typing import List, Optional
from functools import wraps

import aiogram.types as agtypes
import aiomysql
import sqlalchemy as sa
from sqlalchemy.engine.row import Row as SaRow
from sqlalchemy.exc import IntegrityError, OperationalError, DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine
from sqlalchemy.orm import declarative_base
import asyncio

from .enums import ActionName


BaseMySQL = declarative_base()  # Single base for MySQL


# Retry decorator for connection errors
def retry_on_disconnect(max_retries=3, delay=0.5):
    """Retry DB operations on connection errors"""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_error = None
            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except (OperationalError, DBAPIError) as e:
                    last_error = e
                    # Check if it's a connection error
                    error_msg = str(e)
                    if '2013' in error_msg or 'Lost connection' in error_msg or '2006' in error_msg:
                        if attempt < max_retries - 1:
                            await asyncio.sleep(delay * (attempt + 1))
                            continue
                    raise
            raise last_error
        return wrapper
    return decorator


# MySQL models
class BoomUsers(BaseMySQL):
    __tablename__ = 'boom_users'

    id = sa.Column(sa.BigInteger, primary_key=True)
    name = sa.Column(sa.String(255), nullable=True)
    phone = sa.Column(sa.String(20), nullable=True, index=True)
    telegram_id = sa.Column(sa.BigInteger, nullable=True, index=True)


class BoomStores(BaseMySQL):
    __tablename__ = 'boom_stores'

    id = sa.Column(sa.BigInteger, primary_key=True)
    title = sa.Column(sa.String(255), nullable=True)
    main_id = sa.Column(sa.String(255), nullable=True)
    street = sa.Column(sa.String(255), nullable=True)


class BoomOrderDetails(BaseMySQL):
    __tablename__ = 'boom_order_details'

    id = sa.Column(sa.BigInteger, primary_key=True)
    user_id = sa.Column(sa.Integer, nullable=False, index=True)
    order_number = sa.Column(sa.String(50), nullable=True)
    store_id = sa.Column(sa.String(255), nullable=True, index=True)
    created_at = sa.Column(sa.DateTime, nullable=True)


class BoomTickets(BaseMySQL):
    __tablename__ = 'boom_tickets'

    id = sa.Column(sa.BigInteger, primary_key=True, autoincrement=True)
    telegram_id = sa.Column(sa.BigInteger, nullable=False, index=True)
    user_id = sa.Column(sa.Integer, nullable=True, index=True)
    thread_id = sa.Column(sa.BigInteger, nullable=True, index=True)
    subject = sa.Column(sa.String(255), nullable=True)
    store_id = sa.Column(sa.String(255), nullable=True)
    category = sa.Column(sa.String(255), nullable=False)
    order_number = sa.Column(sa.String(50), nullable=True)
    description = sa.Column(sa.Text, nullable=False)
    branch = sa.Column(sa.String(100), nullable=False)
    status = sa.Column(sa.String(20), default='open')
    rating = sa.Column(sa.Integer, nullable=True)
    is_closed = sa.Column(sa.Boolean, default=False)
    created_at = sa.Column(sa.DateTime, default=sa.func.now())
    closed_at = sa.Column(sa.DateTime, nullable=True)


@dataclass
class BoomUser:
    """Dataclass for BoomUsers row"""
    id: int
    name: Optional[str]
    phone: Optional[str]
    telegram_id: Optional[int]


@dataclass
class BoomOrder:
    """Dataclass for BoomOrderDetails row"""
    id: int
    order_number: Optional[str]
    created_at: Optional[datetime.datetime]


@dataclass
class Ticket:
    """Dataclass for BoomTickets row"""
    id: int
    telegram_id: int
    user_id: Optional[int]
    thread_id: Optional[int]
    subject: Optional[str]
    store_id: Optional[str]
    category: str
    order_number: Optional[str]
    description: str
    branch: str
    status: str
    rating: Optional[int]
    is_closed: bool
    created_at: Optional[datetime.datetime]
    closed_at: Optional[datetime.datetime]


class SqlDb:
    """
    A database which uses SQL through SQLAlchemy (MySQL-only).
    Uses a single shared engine for all repositories.
    """
    def __init__(self, mysql_url: str):
        self.mysql_url = mysql_url
        self.engine: Optional[AsyncEngine] = None
        
        if mysql_url:
            # Create ONE engine for all repositories
            self.engine = create_async_engine(
                mysql_url,
                echo=False,
                pool_size=10,              # Connection pool size
                max_overflow=20,           # Max additional connections
                pool_pre_ping=True,        # Test connections before use (CRITICAL!)
                pool_recycle=3600,         # Recycle connections every hour
                pool_timeout=30,           # Timeout waiting for connection
                connect_args={
                    'connect_timeout': 10, # Connection timeout
                    'read_timeout': 30,    # Read timeout
                    'write_timeout': 30,   # Write timeout
                    'charset': 'utf8mb4',
                }
            )
            
            # Pass the shared engine to all repositories
            self.boom_user = SqlBoomUser(self.engine)
            self.tickets = TicketRepo(self.engine)
    
    async def close(self):
        """Close the shared engine (call this on shutdown)"""
        if self.engine:
            await self.engine.dispose()


class SqlRepo:
    """
    Base repository class
    """
    def __init__(self, engine: AsyncEngine):
        self.engine = engine


class SqlBoomUser(SqlRepo):
    """
    Repository for BoomUsers and BoomOrderDetails tables (MySQL).
    """
    def __init__(self, engine: AsyncEngine):
        super().__init__(engine)

    @retry_on_disconnect(max_retries=3)
    async def find_by_phone(self, phone: str) -> Optional[BoomUser]:
        """Find user by phone (O(1) via index). Validates phone as digits/+ prefix."""
        if not (phone.startswith('+') and phone[1:].isdigit() or phone.isdigit()):
            raise ValueError("Invalid phone format")
        phone = phone.lstrip('+')  # Normalize for DB
        async with self.engine.begin() as conn:
            result = await conn.execute(
                sa.select(BoomUsers).where(BoomUsers.phone == phone)
            )
            if row := result.fetchone():
                return BoomUser(
                    id=int(row.id), name=row.name, phone=row.phone, 
                    telegram_id=int(row.telegram_id) if row.telegram_id else None
                )
            return None

    @retry_on_disconnect(max_retries=3)
    async def update_telegram_id(self, user_id: int, telegram_id: int) -> None:
        """Update telegram_id (idempotent)."""
        async with self.engine.begin() as conn:
            await conn.execute(
                sa.update(BoomUsers)
                .where(BoomUsers.id == user_id)
                .values(telegram_id=telegram_id)
            )

    @retry_on_disconnect(max_retries=3)
    async def find_by_telegram_id(self, telegram_id: int) -> Optional[BoomUser]:
        """Find user by telegram_id (O(1) via index)."""
        async with self.engine.begin() as conn:
            result = await conn.execute(
                sa.select(BoomUsers).where(BoomUsers.telegram_id == telegram_id)
            )
            if row := result.fetchone():
                return BoomUser(
                    id=int(row.id), name=row.name, phone=row.phone, 
                    telegram_id=int(row.telegram_id) if row.telegram_id else None
                )
            return None

    @retry_on_disconnect(max_retries=3)
    async def get_recent_orders(self, user_id: int, limit: int = 3) -> List[BoomOrder]:
        """Get recent orders (O(log n) sort, bounded limit)."""
        async with self.engine.begin() as conn:
            result = await conn.execute(
                sa.select(BoomOrderDetails)
                .where(BoomOrderDetails.user_id == user_id)
                .order_by(sa.desc(BoomOrderDetails.created_at))
                .limit(limit)
            )
            return [
                BoomOrder(id=int(r.id), order_number=r.order_number, created_at=r.created_at)
                for r in result.fetchall()
            ]

    @retry_on_disconnect(max_retries=3)
    async def get_store_title(self, store_id: str) -> Optional[str]:
        """Get store title by store_id (O(1))."""
        async with self.engine.begin() as conn:
            result = await conn.execute(
                sa.select(BoomStores.title, BoomStores.main_id, BoomStores.street).where(BoomStores.id == store_id)
            )
            if row := result.fetchone():
                if row.main_id == 'express':
                    return row.street
                return row.title
            return None

    @retry_on_disconnect(max_retries=3)
    async def get_order_by_number(self, order_number: str) -> Optional[dict]:
        """Get order details by order_number including store_id."""
        async with self.engine.begin() as conn:
            result = await conn.execute(
                sa.select(BoomOrderDetails).where(BoomOrderDetails.order_number == order_number)
            )
            if row := result.fetchone():
                return {
                    'id': int(row.id),
                    'user_id': int(row.user_id),
                    'order_number': row.order_number,
                    'store_id': row.store_id,
                    'created_at': row.created_at
                }
            return None


class TicketRepo(SqlRepo):
    """Repository for BoomTickets (O(1) ops via indexes)."""
    def __init__(self, engine: AsyncEngine):
        super().__init__(engine)

    @retry_on_disconnect(max_retries=3)
    async def create(self, telegram_id: int, user_id: Optional[int], category: str, order_number: Optional[str], 
                     description: str, branch: str, thread_id: Optional[int] = None, 
                     subject: Optional[str] = None, store_id: Optional[str] = None) -> int:
        async with self.engine.begin() as conn:
            result = await conn.execute(
                sa.insert(BoomTickets).values(
                    telegram_id=telegram_id, user_id=user_id, category=category, 
                    order_number=order_number, description=description, branch=branch,
                    thread_id=thread_id, subject=subject, store_id=store_id
                )
            )
            ticket_id = result.lastrowid
        return ticket_id

    @retry_on_disconnect(max_retries=3)
    async def update_status(self, ticket_id: str, status: str, closed_at: Optional[datetime.datetime] = None):
        async with self.engine.begin() as conn:
            values = {'status': status}
            if status == 'closed':
                values['is_closed'] = True
                values['closed_at'] = closed_at or sa.func.now()
            elif status in ('open', 'reopened'):
                values['is_closed'] = False
                values['closed_at'] = None
            await conn.execute(
                sa.update(BoomTickets)
                .where(BoomTickets.id == ticket_id)
                .values(**values)
            )

    @retry_on_disconnect(max_retries=3)
    async def get_by_id(self, ticket_id: str) -> Optional[Ticket]:
        """Fetch ticket (O(1))."""
        async with self.engine.begin() as conn:
            result = await conn.execute(
                sa.select(BoomTickets).where(BoomTickets.id == ticket_id)
            )
            if row := result.fetchone():
                return Ticket(
                    id=row.id, telegram_id=int(row.telegram_id), 
                    user_id=int(row.user_id) if row.user_id else None,
                    thread_id=int(row.thread_id) if row.thread_id else None,
                    subject=row.subject, store_id=row.store_id,
                    category=row.category, order_number=row.order_number,
                    description=row.description, branch=row.branch, status=row.status,
                    rating=row.rating, is_closed=row.is_closed,
                    created_at=row.created_at, closed_at=row.closed_at
                )
            return None

    @retry_on_disconnect(max_retries=3)
    async def find_by_thread_id(self, thread_id: int) -> Optional[Ticket]:
        """Find ticket by thread_id (O(1) via index)."""
        async with self.engine.begin() as conn:
            result = await conn.execute(
                sa.select(BoomTickets).where(BoomTickets.thread_id == thread_id)
            )
            if row := result.fetchone():
                return Ticket(
                    id=row.id, telegram_id=int(row.telegram_id), 
                    user_id=int(row.user_id) if row.user_id else None,
                    thread_id=int(row.thread_id) if row.thread_id else None,
                    subject=row.subject, store_id=row.store_id,
                    category=row.category, order_number=row.order_number,
                    description=row.description, branch=row.branch, status=row.status,
                    rating=row.rating, is_closed=row.is_closed,
                    created_at=row.created_at, closed_at=row.closed_at
                )
            return None

    @retry_on_disconnect(max_retries=3)
    async def update_thread_subject(self, ticket_id: str, thread_id: int, subject: str) -> None:
        """Update thread_id and subject (O(1))."""
        async with self.engine.begin() as conn:
            await conn.execute(
                sa.update(BoomTickets)
                .where(BoomTickets.id == ticket_id)
                .values(thread_id=thread_id, subject=subject)
            )

    @retry_on_disconnect(max_retries=3)
    async def close_ticket(self, ticket_id: str) -> None:
        """Close ticket and mark is_closed (O(1))."""
        async with self.engine.begin() as conn:
            await conn.execute(
                sa.update(BoomTickets)
                .where(BoomTickets.id == ticket_id)
                .values(is_closed=True, status='closed', closed_at=sa.func.now())
            )

    @retry_on_disconnect(max_retries=3)
    async def find_last_open_by_user(self, telegram_id: int) -> Optional[Ticket]:
        """Find the last open or reopened ticket for a user by telegram_id."""
        async with self.engine.begin() as conn:
            result = await conn.execute(
                sa.select(BoomTickets)
                .where(
                    BoomTickets.telegram_id == telegram_id, 
                    BoomTickets.is_closed == False,
                    BoomTickets.status.in_(['open', 'reopened'])
                )
                .order_by(sa.desc(BoomTickets.created_at))
                .limit(1)
            )
            if row := result.fetchone():
                return Ticket(
                    id=row.id,
                    telegram_id=int(row.telegram_id),
                    user_id=int(row.user_id) if row.user_id else None,
                    thread_id=int(row.thread_id) if row.thread_id else None,
                    subject=row.subject,
                    store_id=row.store_id,
                    category=row.category,
                    order_number=row.order_number,
                    description=row.description,
                    branch=row.branch,
                    status=row.status,
                    rating=row.rating,
                    is_closed=row.is_closed,
                    created_at=row.created_at,
                    closed_at=row.closed_at
                )
            return None

    @retry_on_disconnect(max_retries=3)
    async def update_rating(self, ticket_id: str, rating: int) -> None:
        """Update ticket rating after closure (O(1))."""
        async with self.engine.begin() as conn:
            await conn.execute(
                sa.update(BoomTickets)
                .where(BoomTickets.id == ticket_id)
                .values(
                    rating=rating,
                    is_closed=True,
                    status='closed',
                    closed_at=sa.func.now()
                )
            )