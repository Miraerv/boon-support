"""
A package for system messages:
technical informing in chats, writing logs
"""
import datetime
import re  # For redaction

import aiogram.types as agtypes
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from .utils import make_short_user_info


def log(func):
    """
    Decorator. Logs actions
    """
    async def wrapper(msg: agtypes.Message, *args, **kwargs):
        # Pop dispatcher to avoid passing it downstream
        kwargs.pop('dispatcher', None)
        # NEW: Pop 'bots' to avoid passing it downstream (likely middleware artifact)
        kwargs.pop('bots', None)
        await msg.bot.log(func.__name__)
        return await func(msg, *args, **kwargs)

    wrapper.__name__ = func.__name__
    return wrapper


def handle_error(func):
    """
    Decorator. Processes any exception in a handler
    """
    async def wrapper(msg: agtypes.Message, *args, **kwargs):
        # Pop dispatcher to avoid passing it downstream
        kwargs.pop('dispatcher', None)
        # NEW: Pop 'bots' to avoid passing it downstream (likely middleware artifact)
        kwargs.pop('bots', None)
        try:
            return await func(msg, *args, **kwargs)
        except TelegramForbiddenError:
            await report_user_ban(msg, func)
        except TelegramBadRequest as exc:
            if 'not enough rights to create a topic' in exc.message:
                await report_cant_create_topic(msg)
        except Exception as exc:
            await msg.bot.log_error(exc)

    wrapper.__name__ = func.__name__
    return wrapper


@log
async def report_user_ban(msg: agtypes.Message, func) -> None:
    bot = msg.bot
    if func.__name__ == 'admin_message':
        if to_msg := msg.reply_to_message:
            # Тот же парсинг, что и выше
            if to_msg.forward_from:
                user_id = to_msg.forward_from.id
                user_name = to_msg.forward_from.full_name
            elif to_msg.text and "Пользователь: " in to_msg.text:
                match = re.search(r'Пользователь:\s*(\d+)', to_msg.text)
                user_id = int(match.group(1)) if match else "Unknown"
                # Имя можно взять из DB по ID, но для простоты — "Unknown" или из from_user
                user_name = to_msg.from_user.full_name if to_msg.from_user else "Unknown"
            else:
                user_id = to_msg.from_user.id
                user_name = to_msg.from_user.full_name if to_msg.from_user else "Unknown"
        else:
            user_id = "Unknown"
            user_name = "Unknown"
        group_id = bot.cfg['admin_group_id']
        await bot.send_message(
            group_id, f'The user banned the bot. User ID: {user_id}, Name: {user_name}',
        )

@log
async def report_cant_create_topic(msg: agtypes.Message) -> None:
    """
    Report when the bot can't create a topic
    """
    user = msg.chat

    await msg.bot.send_message(
        msg.bot.cfg['admin_group_id'],
        (f'New user <b>{make_short_user_info(user=user)}</b> writes to the bot, '
         'but the bot has not enough rights to create a topic.\n\n️️️❗ '
         'Make the bot admin, and give it a "Manage topics" permission.'),
    )


async def stats_to_admin_chat(bots: list) -> None:
    """
    No stats (SQLite removed)
    """
    raise NotImplementedError('No stats (SQLite removed).')