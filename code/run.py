#!/usr/bin/env python
import logging
import os
import sys
from pathlib import Path

import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from aiogram import Dispatcher

from support_bot import SupportBot, destruct_messages, stats_to_admin_chat, register_handlers  # Stubs retained
from support_bot.db import SqlDb  # Added for MySQL init


BASE_DIR = Path(__file__).resolve().parent
BOTS = ()


def setup_logger(level=logging.INFO, log_path=None) -> logging.Logger:
    global logger
    logger = logging.getLogger('support_bot')
    logger.setLevel(level)

    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(level)
    frmtr = logging.Formatter('%(asctime)s %(levelname)s: %(message)s')
    stream_handler.setFormatter(frmtr)
    logger.addHandler(stream_handler)

    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path)
        file_handler.setLevel(level)
        file_handler.setFormatter(frmtr)
        logger.addHandler(file_handler)


def init_bots():
    """
    Create Bot instances. Any command works with them,
    so it's shorter to have them as a global
    """
    global BOTS
    if BOTS:
        return BOTS

    BOTS = []
    mysql_url = os.getenv('DATABASE_URL')
    if mysql_url and mysql_url.startswith('mysql://'):
        mysql_url = mysql_url.replace('mysql://', 'mysql+aiomysql://', 1)

    for name in os.getenv('BOTS_ENABLED').split(','):
        if name := name.strip():
            bot = SupportBot(name, logger)
            if mysql_url:
                bot.db = SqlDb(mysql_url)  # Fixed: Direct init for MySQL-only
            BOTS.append(bot)

    return BOTS


async def start() -> None:
    """
    Create bot instances and run them within a dispatcher
    """
    await start_jobs(BOTS)

    dp = Dispatcher()
    register_handlers(dp)

    logger.info('Started bots: %s', ', '.join([b.name for b in BOTS]))
    await dp.start_polling(*BOTS, polling_timeout=30)


def cmd_makemigrations() -> None:
    """
    Generate migration scripts if there are changes in schema
    """
    logger.info('Stub: No Alembic migrations (MySQL schema user-managed).')


def cmd_migrate() -> None:
    """
    Migrate each bot DB
    """
    logger.info('Stub: No migrations (MySQL schema user-managed).')


async def start_jobs(bots: list) -> None:
    scheduler = AsyncIOScheduler()
    # Removed: stats_to_admin_chat job
    # Removed: destruct_messages job
    scheduler.start()


def main() -> None:
    setup_logger(log_path=BASE_DIR / '..' / 'shared' / 'support_bot.log')

    if not os.environ.get('IS_DOCKER', False):
        load_dotenv(BASE_DIR / '../.env')

    init_bots()

    if 'makemigrations' in sys.argv:
        cmd_makemigrations()
    elif 'migrate' in sys.argv:
        cmd_migrate()
    else:
        asyncio.run(start())


if __name__ == '__main__':
    main()