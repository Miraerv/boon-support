import logging
import os
from pathlib import Path
from typing import Optional

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from .buttons import load_toml
from .const import AdminBtn
from .db import SqlDb  # Added for typing


BASE_DIR = Path(__file__).resolve().parent.parent


class SupportBot(Bot):
    """
    Aiogram Bot Wrapper
    """
    cfg_vars = (
        'admin_group_id', 'hello_msg', 'first_reply',
        'hello_ps', 'destruct_user_messages_for_user', 'destruct_bot_messages_for_user'
    )

    def __init__(self, name: str, logger: logging.Logger):
        self.name = name
        self._logger = logger

        self.botdir.mkdir(parents=True, exist_ok=True)
        token, self.cfg = self._read_config()
        self._load_menu()

        super().__init__(token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        self.db: Optional[SqlDb] = None  # Added: Type hint for MySQL-only DB

    @property
    def botdir(self) -> Path:
        return BASE_DIR / '..' / 'shared' / self.name

    def _read_config(self) -> tuple[str, dict]:
        """
        Read a bot token and a config with other vars
        """
        cfg = {
            'name': self.name,
            'hello_msg': 'Hello! Write your message',
            'first_reply': (
                "We have received your message. We'll get back to you as soon as we can. "
                "Please don't delete the chat so we can send you a reply."
            ),
            'hello_ps': '\n\n<i>The bot is created by @moladzbel</i>',
        }
        for var in self.cfg_vars:
            envvar = os.getenv(f'{self.name}_{var.upper()}')
            if envvar is not None:
                cfg[var] = envvar

        # New: MySQL URL from env
        cfg['mysql_url'] = os.getenv('DATABASE_URL')
        if cfg['mysql_url'] and cfg['mysql_url'].startswith('mysql://'):
            cfg['mysql_url'] = cfg['mysql_url'].replace('mysql://', 'mysql+aiomysql://', 1)

        # validate and convert destruction vars
        for var in 'destruct_user_messages_for_user', 'destruct_bot_messages_for_user':
            if var in cfg:
                cfg[var] = int(cfg[var])
                if not 1 <= cfg[var] <= 47:
                    raise ValueError(f'{var} must be between 1 and 47 (hours)')

        cfg['hello_msg'] += cfg['hello_ps']
        return os.getenv(f'{self.name}_TOKEN'), cfg

    async def log(self, message: str, level=logging.INFO) -> None:
        self._logger.log(level, f'{self.name}: {message}')

    async def log_error(self, exception: Exception, traceback: bool = True) -> None:
        self._logger.error(str(exception), exc_info=traceback)

    def _load_menu(self) -> None:
        self.menu = load_toml(self.botdir / 'menu.toml')
        if self.menu:
            self.menu['answer'] = self.cfg['hello_msg']

        self.admin_menu = {
            AdminBtn.broadcast: {'label': '📢 Broadcast to all subscribers',
                                 'answer': ("Send here a message to broadcast, and then I'll ask "
                                            "for confirmation")},
            AdminBtn.del_old_topics: {'label': '🧹 Delete topics older than 2 weeks',
                                      'answer': 'No local topics to delete.'},
        }

    # New: Close DB repos
    async def close_db(self):
        if self.db:
            await self.db.boom_user.close()
            await self.db.tickets.close()