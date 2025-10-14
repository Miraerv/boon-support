import asyncio

import aiogram.types as agtypes
from aiogram import Dispatcher
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.base import StorageKey

from .const import AdminBtn
from .informing import handle_error, log


class BroadcastForm(StatesGroup):
    message = State()
    confirm = State()


@log
@handle_error
async def del_old_topics(call: agtypes.CallbackQuery):
    """
    Admin action - no local topics to delete
    """
    raise NotImplementedError('No local topics to delete (SQLite removed).')


@log
@handle_error
async def admin_broadcast_start(call: agtypes.CallbackQuery, dispatcher: Dispatcher) -> None:
    """
    Start broadcasting flow - ask for a message to broadcast
    """
    msg = call.message
    bot = msg.bot

    key = StorageKey(bot_id=bot._me.id, chat_id=msg.chat.id, user_id=call.from_user.id)
    state = FSMContext(dispatcher.storage, key)

    await state.set_state(BroadcastForm.message)
    await msg.answer(bot.admin_menu[AdminBtn.broadcast]['answer'])


@log
@handle_error
async def admin_broadcast_ask_confirm(msg: agtypes.Message, state: FSMContext,
                                      *args, **kwargs) -> None:
    """
    Middle of the broadcasting flow - confirmation
    """
    from .buttons import build_confirm_menu, send_new_msg_with_keyboard
    bot = msg.bot

    try:
        await bot.copy_message(msg.chat.id, from_chat_id=msg.chat.id, message_id=msg.message_id)
    except TelegramBadRequest:
        return await msg.answer("This type of message can't be sent, sorry 🥺. Try again.")

    await state.update_data(message=msg.message_id)
    await state.set_state(BroadcastForm.confirm)
    await asyncio.sleep(0.1)

    text = 'Send this 👆 message to all the bot users?'
    await send_new_msg_with_keyboard(bot, bot.cfg['admin_group_id'], text, build_confirm_menu())


@log
@handle_error
async def admin_broadcast_finish(call: agtypes.CallbackQuery, state: FSMContext,
                                 *args, **kwargs) -> None:
    """
    End of the broadcasting flow - send the message or forget it
    """
    from .buttons import CBD

    msg = call.message
    bot = msg.bot
    cbd = CBD.unpack(call.data)
    state_data = await state.get_data()

    if cbd.code == 'yes':
        text = 'Broadcasting the message...'
        await bot.edit_message_text(chat_id=msg.chat.id, message_id=cbd.msgid, text=text)

        success_count = 0
        users = []  # Stub: no persistent users
        for i, user in enumerate(users):
            try:
                await bot.copy_message(user.user_id, from_chat_id=msg.chat.id,
                                       message_id=state_data['message'])
                success_count += 1
            except TelegramForbiddenError as exc:
                pass

            if len(users) > 50 and i != 0 and i % (len(users) // 10) == 0:
                await bot.log(f'{i}/{len(users)} processed for broadcasting')

        res_str = f'{success_count}/{len(users)}'
        await bot.log(f'Broadcasting is done: {res_str}')
        await msg.answer(f'Broadcasting is done 🫡. {res_str} users received the message.')

    elif cbd.code == 'no':
        text = 'Broadcasting canceled'
        await bot.edit_message_text(chat_id=msg.chat.id, message_id=cbd.msgid, text=text)

    await state.clear()
    return await call.answer()
