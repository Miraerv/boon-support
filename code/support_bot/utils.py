import datetime
import html

import aiogram.types as agtypes

from .const import MsgType

async def make_user_info(user: agtypes.User, bot=None, tguser=None) -> str:
    """
    Text representation of a user.
    """
    name = f'<b>{html.escape(user.full_name)}</b>'
    username = f'@{user.username}' if user.username else 'No username'
    userid = f'<b>User TG ID</b>: <code>{user.id}</code>'
    fields = [name, username, userid]
    
    # Add internal DB ID and phone if the user is found in our system
    if tguser:
        fields.append(f'<b>System ID</b>: <code>{tguser.id}</code>')
        if tguser.phone:
            fields.append(f'<b>Phone</b>: <code>{tguser.phone}</code>')

    if lang := getattr(user, 'language_code', None):
        fields.append(f'Language code: {lang}')
    if premium := getattr(user, 'is_premium', None):
        fields.append(f'Premium: {premium}')

    if bot:
        try:
            uinfo = await bot.get_chat(user.id)
            if uinfo.bio:
                fields.append(f'<b>Bio</b>: {html.escape(uinfo.bio)}')

            if uinfo.active_usernames and len(uinfo.active_usernames) > 1:
                fields.append(f'Active usernames: @{", @".join(uinfo.active_usernames)}')
        except Exception:
            # Bot might not be able to get chat info (e.g., due to privacy settings)
            pass

    # The problematic 'subject' check has been removed.

    return '\n\n'.join(fields)


def make_short_user_info(user: agtypes.User | None=None, tguser=None) -> str:
    """
    Short text representation of a user
    """
    if user:
        user_id = user.id
    elif tguser:
        user_id = tguser.user_id
        user = tguser

    fullname = html.escape(user.full_name or '')
    tech_part = f'@{user.username}, id {user_id}' if user.username else f'id {user_id}'
    return f'{fullname} ({tech_part})'


def determine_msg_type(msg: agtypes.Message) -> str:
    """
    Determine a type of the message by inspecting its content
    """
    if msg.photo:
        return MsgType.photo
    elif msg.video:
        return MsgType.video
    elif msg.animation:
        return MsgType.animation
    elif msg.sticker:
        return MsgType.sticker
    elif msg.audio:
        return MsgType.audio
    elif msg.voice:
        return MsgType.voice
    elif msg.document:
        return MsgType.document
    elif msg.video_note:
        return MsgType.video_note
    elif msg.contact:
        return MsgType.contact
    elif msg.location:
        return MsgType.location
    elif msg.venue:
        return MsgType.venue
    elif msg.poll:
        return MsgType.poll
    elif msg.dice:
        return MsgType.dice
    else:
        return MsgType.regular_or_other


async def destruct_messages(bots: list) -> None:
    """
    No message deletion (SQLite removed)
    """
    raise NotImplementedError('No message deletion (SQLite removed).')


async def save_for_destruction(msg, bot, chat_id=None):
    """
    No-op
    """
    raise NotImplementedError('No message destruction (SQLite removed).')