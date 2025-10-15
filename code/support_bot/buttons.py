"""
Display menu with buttons according to menu.toml file,
handle buttons actions
"""
from pathlib import Path
from typing import List, Dict, Any

import aiogram.types as agtypes
import toml
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder  # Consolidated import
from aiogram.fsm.context import FSMContext

from .admin_actions import admin_broadcast_start, del_old_topics
from .const import MSG_TEXT_LIMIT, AdminBtn, ButtonMode, MenuMode
from .informing import handle_error, log
# Removed: save_for_destruction (no destruct)
from .db import BoomOrder, Ticket  # Updated import for typing
import datetime

def load_toml(path: Path) -> dict | None:
    """
    Read toml file
    """
    if path.is_file():
        with open(path) as f:
            return toml.load(f)


class CBD(CallbackData, prefix='_'):
    """
    Callback Data
    """
    path: str  # separated inside by '.'
    code: str  # button identifier after the path
    msgid: int = 0  # id of a message with this button
    ticket_id: str = ''  # New: For ticket actions


# New: Short CallbackData for tickets (fits <64 bytes)
class TicketCBD(CallbackData, prefix='t'):
    action: str  # 'connect', 'close', 'feedback_yes', 'feedback_no'
    ticket_id: int


class Button:
    """
    Wrapper over an inline keyboard button
    """
    def __init__(self, content):
        self.content = content
        self._recognize_mode()

        empty_answer_allowed = self.mode in (ButtonMode.link, ButtonMode.file)
        self.answer = _extract_answer(content, empty=empty_answer_allowed)

    def _recognize_mode(self) -> None:
        if 'link' in self.content:
            self.mode = ButtonMode.link
        elif 'file' in self.content:
            self.mode = ButtonMode.file
        elif any([isinstance(v, dict) and 'label' in v for v in self.content.values()]):
            self.mode = ButtonMode.menu
        elif 'subject' in self.content:
            self.mode = ButtonMode.subject
        elif 'answer' in self.content:
            self.mode = ButtonMode.answer

    def as_inline(self, callback_data : str | None=None) -> InlineKeyboardButton:
        if self.mode in (ButtonMode.file, ButtonMode.answer, ButtonMode.menu, ButtonMode.subject):
            return InlineKeyboardButton(text=self.content['label'], callback_data=callback_data)
        elif self.mode == ButtonMode.link:
            return InlineKeyboardButton(text=self.content['label'], url=self.content['link'])
        raise ValueError('Unexpected button mode')


def _extract_answer(menu: dict, empty: bool=False) -> str:
    answer = (menu.get('answer') or '')[:MSG_TEXT_LIMIT]
    if not empty:
        answer = answer or '👀'
    return answer


def _create_button(content):
    """
    Button factory
    """
    if 'label' in content:
        return Button(content)


def _get_kb_builder(menu: dict, msgid: int, path: str='') -> InlineKeyboardBuilder:
    """
    Construct an InlineKeyboardBuilder object based on a given menu structure.
    Args:
        menu (dict): A dict with menu items to display.
        msgid (int): message_id to place into callback data.
        path (str, optional): A path to remember in callback data,
            to be able to find an answer for a menu item.
    """
    builder = InlineKeyboardBuilder()

    for key, val in menu.items():
        if btn := _create_button(val):
            cbd = CBD(path=path, code=key, msgid=msgid).pack()
            if menu.get('menumode') == MenuMode.row:
                builder.button(text=btn.content['label'], callback_data=cbd)
            else:
                builder.row(btn.as_inline(cbd))

    if path:  # build bottom row with navigation
        btns = []
        cbd = CBD(path='', code='', msgid=msgid).pack()
        btns.append(InlineKeyboardButton(text='🏠', callback_data=cbd))

        if '.' in path:
            spl = path.split('.')
            cbd = CBD(path='.'.join(spl[:-2]), code=spl[-2], msgid=msgid).pack()
            btns.append(InlineKeyboardButton(text='←', callback_data=cbd))

        builder.row(*btns)

    return builder


def _find_menu_item(menu: dict, cbd: CallbackData) -> [dict, str]:
    """
    Find a button info in bot menu tree by callback data.
    """
    target = menu
    pathlist = []
    for lvlcode in cbd.path.split('.'):
        if lvlcode:
            pathlist.append(lvlcode)
            target = target.get(lvlcode)

    pathlist.append(cbd.code)
    return target.get(cbd.code), '.'.join(pathlist)


@log
@handle_error
async def user_btn_handler(call: agtypes.CallbackQuery, *args, **kwargs):
    """
    A callback for any button shown to a user.
    """
    msg = call.message
    bot, chat = msg.bot, msg.chat
    if not call.data.startswith('_'):  # Skip non-CBD callbacks (e.g., rating_)
        await call.answer()
        return
    cbd = CBD.unpack(call.data)
    menuitem, path = _find_menu_item(bot.menu, cbd)
    sentmsg = None

    if not cbd.path and not cbd.code:  # main menu
        sentmsg = await edit_or_send_new_msg_with_keyboard(bot, chat.id, cbd, bot.menu)

    elif btn := _create_button(menuitem):
        if btn.mode == ButtonMode.menu:
            sentmsg = await edit_or_send_new_msg_with_keyboard(bot, chat.id, cbd, menuitem, path)
        elif btn.mode == ButtonMode.file:
            sentmsg = await send_file(bot, chat.id, menuitem)
        elif btn.mode == ButtonMode.answer:
            sentmsg = await msg.answer(btn.answer)
        elif btn.mode == ButtonMode.subject:
            sentmsg = await set_subject(bot, chat, menuitem)

    # Removed: save_for_destruction

    return await call.answer()


@log
@handle_error
async def admin_btn_handler(call: agtypes.CallbackQuery, *args, **kwargs):
    """
    A callback for any button shown in admin group.
    """
    # Updated: Detect prefix and unpack accordingly
    if call.data.startswith('t::'):
        cbd = TicketCBD.unpack(call.data)
        if cbd.action == 'connect':
            await handle_connect(call, cbd.ticket_id)
        elif cbd.action == 'close':
            await handle_close(call, cbd.ticket_id)
    else:
        cbd = CBD.unpack(call.data)

        if cbd.code == AdminBtn.del_old_topics:
            await del_old_topics(call)
        elif cbd.code == AdminBtn.broadcast:
            await admin_broadcast_start(call, kwargs['dispatcher'])

    return await call.answer()


# New: Admin ticket handlers (stub session as notify + direct reply enable)
async def handle_connect(call: agtypes.CallbackQuery, ticket_id: str):
    bot = call.message.bot
    ticket = await bot.db.tickets.get_by_id(ticket_id)
    if ticket and ticket.status == 'open':
        await bot.send_message(ticket.user_id, "Оператор подключается к сессии. Ожидайте ответа.")
        await call.message.edit_text(call.message.text + "\n\nСессия открыта: оператор подключен.")
        # Stub: Direct replies already enabled via copy_to in admin_message


async def handle_close(call: agtypes.CallbackQuery, ticket_id: str):
    bot = call.message.bot
    now = datetime.datetime.now()
    await bot.db.tickets.update_status(ticket_id, 'closed', now)
    ticket = await bot.db.tickets.get_by_id(ticket_id)
    if ticket:
        await call.message.edit_text(call.message.text + "\n\nТикет закрыт.")
        # Trigger user feedback
        await bot.send_message(
            ticket.user_id,
            "Подскажите, пожалуйста, удалось ли решить Ваш вопрос?",
            reply_markup=build_feedback_keyboard(ticket_id)
        )


# Updated: Use TicketCBD for short packing
def build_ticket_keyboard(ticket_id: str) -> InlineKeyboardBuilder:
    """Admin keyboard for ticket: Connect/Close."""
    builder = InlineKeyboardBuilder()
    connect_cbd = TicketCBD(action='connect', ticket_id=ticket_id).pack()
    close_cbd = TicketCBD(action='close', ticket_id=ticket_id).pack()
    builder.row(
        InlineKeyboardButton(text="Связаться", callback_data=connect_cbd),
        InlineKeyboardButton(text="Закрыть", callback_data=close_cbd)
    )
    return builder


# Updated: Use TicketCBD
def build_feedback_keyboard(ticket_id: str) -> InlineKeyboardBuilder:
    """User feedback: Yes/No."""
    builder = InlineKeyboardBuilder()
    yes_cbd = TicketCBD(action='feedback_yes', ticket_id=ticket_id).pack()
    no_cbd = TicketCBD(action='feedback_no', ticket_id=ticket_id).pack()
    builder.row(
        InlineKeyboardButton(text="Да, закрыт", callback_data=yes_cbd),
        InlineKeyboardButton(text="Нет, не закрыт", callback_data=no_cbd)
    )
    return builder


def build_closure_confirmation_keyboard(ticket_id: str) -> InlineKeyboardBuilder:
    """User confirmation: Issue resolved or not."""
    builder = InlineKeyboardBuilder()
    yes_cbd = TicketCBD(action='closure_yes', ticket_id=ticket_id).pack()
    no_cbd = TicketCBD(action='closure_no', ticket_id=ticket_id).pack()
    builder.row(
        InlineKeyboardButton(text="Да, закрыт", callback_data=yes_cbd),
        InlineKeyboardButton(text="Нет, не закрыт", callback_data=no_cbd)
    )
    return builder


async def send_file(bot, chat_id: int, menuitem: dict) -> agtypes.Message:
    """
    Shortcut for sending a file on a button press.
    """
    fpath = bot.botdir / 'files' / menuitem['file']
    if fpath.is_file():
        doc = agtypes.FSInputFile(fpath)
        caption = _extract_answer(menuitem, empty=True)
        return await bot.send_document(chat_id, document=doc, caption=caption)

    raise FileNotFoundError(fpath.resolve())


async def set_subject(bot, user: agtypes.User, menuitem: dict) -> agtypes.Message:
    """
    Set the chosen subject to the user and report that.
    """
    newsubj = menuitem['subject']
    group_id = bot.cfg['admin_group_id']

    answer = (menuitem.get('answer') or '')[:MSG_TEXT_LIMIT]
    answer = answer or f'Please write your question about "{menuitem["label"]}"'
    usrmsg = await bot.send_message(user.id, text=answer)

    # Stub: no tguser update or thread notification (SQLite removed)

    return usrmsg


async def edit_or_send_new_msg_with_keyboard(
        bot, chat_id: int, cbd: CallbackData, menu: dict, path: str='') -> agtypes.Message:
    """
    Shortcut to edit a message, or,
    if it's not possible, send a new message.
    """
    text = _extract_answer(menu)
    try:
        markup = _get_kb_builder(menu, cbd.msgid, path).as_markup()
        return await bot.edit_message_text(chat_id=chat_id, message_id=cbd.msgid, text=text,
                                           reply_markup=markup)
    except TelegramBadRequest:
        return await send_new_msg_with_keyboard(bot, chat_id, text, menu, path)


async def send_new_msg_with_keyboard(
        bot, chat_id: int, text: str, menu: dict | None, path: str='') -> agtypes.Message:
    """
    Shortcut to send a message with a keyboard.
    """
    sentmsg = await bot.send_message(chat_id, text=text, disable_web_page_preview=True)
    if menu:
        markup = _get_kb_builder(menu, sentmsg.message_id, path).as_markup()
        await bot.edit_message_text(chat_id=chat_id, message_id=sentmsg.message_id, text=text,
                                    reply_markup=markup)
    return sentmsg


def build_confirm_menu(yes_answer: str='Confirmed', no_answer: str='Canceled') -> dict:
    """
    Shortcut to build typical confirmation keyboard
    """
    menu = {
        'yes': {'label': '✅ Yes', 'answer': yes_answer},
        'no': {'label': '🚫 No', 'answer': no_answer},
        'menumode': MenuMode.row,
    }
    return menu

def build_rating_keyboard(ticket_id: str) -> InlineKeyboardBuilder:
    """Клавиатура для оценки обращения (1–5 звёзд)."""
    builder = InlineKeyboardBuilder()
    stars = [
        ("⭐⭐⭐⭐⭐", 5),
        ("⭐⭐⭐⭐", 4),
        ("⭐⭐⭐", 3),
        ("⭐⭐", 2),
        ("⭐", 1),
    ]
    for emoji, rating in stars:
        builder.button(text=emoji, callback_data=f"rate:{ticket_id}:{rating}")
    builder.adjust(1)
    return builder


# New ReplyKeyboard functions for support flow
def get_share_phone_keyboard() -> ReplyKeyboardMarkup:
    """Keyboard to request phone contact."""
    builder = ReplyKeyboardBuilder()
    button = KeyboardButton(text="Поделиться номером телефона 📲", request_contact=True)
    builder.row(button)
    return builder.as_markup(resize_keyboard=True, one_time_keyboard=True)


def get_main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Main menu after phone login."""
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="Категории обращения"))
    builder.row(KeyboardButton(text="Мои обращения"))
    builder.row(KeyboardButton(text="Частые вопросы"))
    return builder.as_markup(resize_keyboard=True, one_time_keyboard=False)


def get_categories_keyboard() -> ReplyKeyboardMarkup:
    """Categories sub-menu."""
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="Вопрос по заказу"), KeyboardButton(text="Где мой заказ"))
    builder.row(KeyboardButton(text="Другой вопрос"))
    builder.row(KeyboardButton(text="Частые вопросы"))
    return builder.as_markup(resize_keyboard=True, one_time_keyboard=True)


def get_faq_keyboard() -> ReplyKeyboardMarkup:
    """FAQ menu."""
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="Как узнать статус заказа"))
    builder.row(KeyboardButton(text="Как сделать возврат"))
    builder.row(KeyboardButton(text="Назад ⏪"))
    return builder.as_markup(resize_keyboard=True, one_time_keyboard=True)


def get_other_categories_keyboard() -> ReplyKeyboardMarkup:
    """Other categories."""
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="Задержка доставки заказа"))
    builder.row(KeyboardButton(text="Вопрос по заказу"))
    builder.row(KeyboardButton(text="Другой вопрос"))
    builder.row(KeyboardButton(text="Назад ⏪"))
    return builder.as_markup(resize_keyboard=True, one_time_keyboard=True)


async def get_orders_keyboard(orders: List[BoomOrder], category: str, state: FSMContext) -> ReplyKeyboardMarkup:
    """Dynamic orders keyboard (n<=3)."""
    builder = ReplyKeyboardBuilder()
    
    # Создаем маппинг текста кнопки -> order_number
    orders_map = {}
    
    if not orders:
        builder.row(KeyboardButton(text="Другой вопрос"))
        builder.row(KeyboardButton(text="Назад ⏪"))
    else:
        for idx, order in enumerate(orders):
            if order.created_at:
                date_str = order.created_at.strftime('%d.%m.%Y')
                time_str = order.created_at.strftime('%H:%M')
                if idx == 0:
                    text = f"Последний заказ от {date_str} {time_str}"
                else:
                    text = f"Заказ №{order.order_number} от {date_str} {time_str}"
                
                # Сохраняем маппинг
                orders_map[text] = order.order_number
                builder.row(KeyboardButton(text=text))
        
        builder.row(KeyboardButton(text="Другой вопрос"))
        builder.row(KeyboardButton(text="Назад ⏪"))
    
    # Сохраняем маппинг в state
    await state.update_data(orders_map=orders_map)
    
    return builder.as_markup(resize_keyboard=True, one_time_keyboard=True)


def get_remove_keyboard() -> ReplyKeyboardMarkup:
    """Remove reply keyboard."""
    return ReplyKeyboardRemove()
