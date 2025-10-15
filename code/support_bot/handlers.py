# handlers.py (updated)

import aiogram.types as agtypes
from aiogram import Dispatcher, F
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from zoneinfo import ZoneInfo
import datetime
from .admin_actions import BroadcastForm, admin_broadcast_ask_confirm, admin_broadcast_finish
from .buttons import (
    admin_btn_handler, send_new_msg_with_keyboard, user_btn_handler,
    get_share_phone_keyboard, get_categories_keyboard,
    get_faq_keyboard, get_orders_keyboard, get_remove_keyboard,
    build_feedback_keyboard, build_rating_keyboard, build_start_over_keyboard, TicketCBD
)
from .informing import handle_error, log
from .filters import (
    ACommandFilter, BtnInAdminGroup, BtnInPrivateChat, BotMention, InAdminGroup,
    GroupChatCreatedFilter, NewChatMembersFilter, PrivateChatFilter,
    ReplyToBotInGroupForwardedFilter,
)
from .utils import make_user_info
from .db import BoomUser, Ticket
from .const import SupportFlow


@log
@handle_error
async def cmd_start(msg: agtypes.Message, state: FSMContext, *args, **kwargs) -> None:
    """Start command: if user already registered, skip phone request."""
    bot = msg.bot
    db = bot.db
    sender_id = kwargs.get("user_id") or msg.from_user.id

    # Проверяем, есть ли пользователь в базе
    user = await db.boom_user.find_by_telegram_id(sender_id)

    if user and user.phone:
        # Пользователь уже зарегистрирован — приветствуем и открываем меню
        greeting = (
            "Здравствуйте, это служба заботы о клиентах Boon Market 🩷 Чтобы мы быстрее помогли, выберите тему обращения"
        )
        await state.set_state(SupportFlow.category)
        await msg.answer(greeting, reply_markup=get_categories_keyboard())
    else:
        # Телефона нет — просим поделиться номером
        await state.set_state(SupportFlow.waiting_phone)
        await msg.answer(
            "Поделитесь номером телефона, чтобы начать работу со службой заботы.",
            reply_markup=get_share_phone_keyboard()
        )



async def _create_ticket_thread(msg: agtypes.Message, subject: str, ticket_info: str) -> int:
    """Create a new topic for the ticket with structured info"""
    group_id = msg.bot.cfg['admin_group_id']
    bot = msg.bot

    response = await bot.create_forum_topic(group_id, subject)
    thread_id = response.message_thread_id

    await bot.send_message(group_id, ticket_info, message_thread_id=thread_id, parse_mode='HTML')
    return thread_id


async def _group_hello(msg: agtypes.Message):
    """Send group hello message to a group"""
    group = msg.chat
    text = f'Hello!\nID of this group: <code>{group.id}</code>'
    if not group.is_forum:
        text += '\n\n⚠️ Please enable topics in the group settings. This will also change its ID.'
    await msg.bot.send_message(group.id, text)


@log
@handle_error
async def added_to_group(msg: agtypes.Message, *args, **kwargs):
    """Report group ID when added to a group"""
    for member in msg.new_chat_members:
        if member.id == msg.bot.id:
            await _group_hello(msg)
            break


@log
@handle_error
async def group_chat_created(msg: agtypes.Message, *args, **kwargs):
    """Report group ID when a group with the bot is created"""
    await _group_hello(msg)


@log
@handle_error
async def user_message(msg: agtypes.Message, state: FSMContext, *args, **kwargs) -> None:
    """Handle user input: if description state, create ticket; else forward to existing ticket."""
    bot = msg.bot
    group_id = bot.cfg['admin_group_id']
    sender_id = msg.from_user.id
    now_yakutsk = datetime.datetime.now(ZoneInfo("Asia/Yakutsk"))
    yakutsk_hour = now_yakutsk.hour
    data = await state.get_data()
    state_name = await state.get_state()

    # Find user (but don't create if not found)
    user = await bot.db.boom_user.find_by_telegram_id(sender_id)
    user_id = user.id if user else None
    phone_number = user.phone if user else "Неизвестно"
    display_name = user.name if user and user.name != "Гость" else msg.from_user.full_name
    branch = "Россия" if user and user.phone and user.phone.startswith('7') else "Неизвестно"
    
    # Check if user has an open or reopened ticket first
    ticket = await bot.db.tickets.find_last_open_by_user(sender_id)
    
    # If we're in description state AND no open ticket exists, create new ticket
    if state_name == SupportFlow.description and not ticket:
        category = data.get('category', 'unknown')
        order_number = data.get('order', 'не указан')
        description = msg.text or msg.caption or 'No text'
        
        # Get order details to extract store_id (only if user exists and order specified)
        store_id = None
        store_title = None
        if order_number and order_number != 'не указан' and user_id:
            order_details = await bot.db.boom_user.get_order_by_number(order_number)
            if order_details and order_details.get('store_id'):
                store_id = order_details['store_id']
                store_title = await bot.db.boom_user.get_store_title(store_id)
        
        # Create ticket first without thread_id
        ticket_id = await bot.db.tickets.create(
            telegram_id=sender_id,
            user_id=user_id,
            category=category,
            order_number=order_number if order_number != 'не указан' else None,
            description=description,
            branch=branch,
            store_id=store_id
        )

        display_name_with_id = f"№{ticket_id} | {user.name if user and user.name != 'Гость' else msg.from_user.full_name}"

        
        # Build subject
        if user_id:
            subject_parts = []
            if store_title:
                subject_parts.append(store_title)
            if order_number and order_number != 'не указан':
                subject_parts.append(order_number)
            subject_parts.append(display_name_with_id)
            subject = ': '.join(subject_parts)
        else:
            subject = f"Незарегистрированный пользователь: {display_name_with_id} ({category})"
        
        # Build structured ticket info
        store_display = store_title if store_title else "Не указан"
        order_display = order_number if order_number != 'не указан' else "Не указан"
        
        ticket_info = (
            f"<b>Имя:</b> {display_name}\n"
            f"<b>Номер телефона:</b> {phone_number}\n"
            f"<b>Номер обращения:</b> №{ticket_id}\n"
            f"<b>Категория:</b> {category}\n"
            f"<b>Номер заказа:</b> {order_display}\n"
            f"<b>Филиал/Магазин:</b> {store_display}\n"
            f"<b>Описание:</b> {description}\n\n"
            f"<i>Чтобы ответить пользователю используйте функцию “Ответить↩️</i>"
            f"<i>Чтобы закрыть обращение отправьте “/close” </i>"
        )
        
        # Create new thread for this ticket
        thread_id = await _create_ticket_thread(msg, subject, ticket_info)
        
        # Update ticket with thread_id and subject
        await bot.db.tickets.update_thread_subject(ticket_id, thread_id, subject)

        # Send to user
        if 8 <= yakutsk_hour <= 23:
            user_response = f"Мы получили Ваше обращение, спасибо! Наш оператор уже видит запрос и скоро с Вами свяжется 😊  Пожалуйста, ожидайте ответа - обычно это займет немного времени 🙌"
        else:
            user_response = f"Мы получили Ваше обращение, спасибо! График работы техподдержки: с 08:00 до 23:00. Пожалуйста, ожидайте ответа - мы ответим в рабочее время."
        
        # Keep user in active ticket state - clear state to allow free messaging
        await bot.send_message(msg.chat.id, user_response, reply_markup=get_remove_keyboard())
        await state.clear()  # Clear state so user can freely message

    else:
        try:
            ticket = await bot.db.tickets.find_last_open_by_user(sender_id)
        except Exception as e:
            await bot.log_error(f"DB error while fetching ticket for user {sender_id}: {e}")
            await msg.answer("Произошла ошибка при обращении к базе данных. Попробуйте позже.")
            return

    # --- 1. Тикет найден ---
        if ticket:
            # Безопасная проверка статуса
            is_closed = getattr(ticket, "is_closed", False)
            status = getattr(ticket, "status", None)

        # --- 1.1. Переоткрытие тикета ---
            if is_closed or status == "closed":
                try:
                    await bot.db.tickets.update_status(ticket.id, "reopened")
                    await bot.log(f"Reopened ticket #{ticket.id} for user {sender_id}")
                except Exception as e:
                    await bot.log_error(f"Failed to reopen ticket #{ticket.id}: {e}")
                    await msg.answer("Произошла ошибка при попытке переоткрыть обращение. Попробуйте позже.")
                    return

        # --- 1.2. Проверяем наличие темы (thread_id) ---
            if not getattr(ticket, "thread_id", None):
                await msg.answer(
                    "Ваше обращение найдено, но оно не связано с темой чата. "
                    "Пожалуйста, создайте новое обращение через /start.",
                )
                return

        # --- 1.3. Пытаемся форвардить сообщение ---
            try:
                await msg.forward(group_id, message_thread_id=ticket.thread_id)
            except TelegramBadRequest as e:
                await bot.log_error(f"TelegramBadRequest forwarding to thread {ticket.thread_id}: {e}")
                await msg.answer(
                    "Не удалось отправить сообщение в тему поддержки. "
                    "Пожалуйста, создайте новое обращение через /start."
                )
            except TelegramForbiddenError:
                await bot.log_error(f"Bot was blocked or restricted while forwarding user {sender_id}")
                await msg.answer("Бот не может переслать ваше сообщение. Попробуйте позже.")
            except Exception as e:
                await bot.log_error(f"Unexpected forwarding error for ticket #{ticket.id}: {e}")
                await msg.answer("Произошла ошибка при пересылке сообщения. Попробуйте позже.")

    # --- 2. Тикет не найден ---
        else:
            await msg.answer(
                "Чтобы создать новое обращение, пожалуйста, нажмите /start и следуйте инструкциям."
            )



@log
@handle_error
async def admin_message(msg: agtypes.Message, *args, **kwargs) -> None:
    """Copy admin reply to a user via ticket thread_id"""
    bot = msg.bot

    if msg.reply_to_message and msg.message_thread_id:
        ticket = await bot.db.tickets.find_by_thread_id(msg.message_thread_id)
        if ticket and ticket.telegram_id:
            target_id = ticket.telegram_id
            await bot.log(f"Target from ticket: {target_id}")
            try:
                await msg.copy_to(target_id)
                await bot.log(f"Reply copied to user {target_id}")
            except TelegramForbiddenError as e:
                thread_id = msg.message_thread_id
                bot_info = await bot.get_me()
                if "bots can't send messages to bots" in e.message:
                    error_msg = f"Невозможно отправить сообщение боту {target_id}. Боты не могут общаться друг с другом."
                else:
                    error_msg = f"Ответ не удалось отправить пользователю {target_id} (вероятно, заблокировал бота).\n" \
                                f"Попросите разблокировать @{bot_info.username} в настройках Telegram."
                await bot.send_message(msg.chat.id, error_msg, message_thread_id=thread_id)
                return
            except Exception as e:
                await bot.log_error(e)
        else:
            await bot.log("No ticket found for thread_id — skipping copy")


@log
@handle_error
async def cmd_close_ticket(msg: agtypes.Message, *args, **kwargs) -> None:
    """Admin command to close ticket and request rating from user"""
    bot = msg.bot
    thread_id = msg.message_thread_id

    if not thread_id:
        await msg.answer("Команда /close должна использоваться внутри темы тикета.")
        return

    ticket = await bot.db.tickets.find_by_thread_id(thread_id)
    if not ticket:
        await msg.answer("Тикет не найден для этой темы")
        return

    if ticket.is_closed:
        await msg.answer(f"Тикет №{ticket.id} уже закрыт")
        return

    await bot.db.tickets.close_ticket(ticket.id)
    await msg.answer(f"Оценка обращения отправлено пользователю")

    from .buttons import build_closure_confirmation_keyboard
    confirmation_text = "Подскажите, пожалуйста, удалось ли решить Ваш вопрос?"
    try:
        await bot.send_message(
            ticket.telegram_id,
            confirmation_text,
            reply_markup=build_closure_confirmation_keyboard(ticket.id).as_markup()
        )
    except Exception as e:
        await bot.log_error(e)
        await msg.answer(f"Не удалось отправить запрос оценки пользователю {ticket.telegram_id}")


@log
@handle_error
async def mention_in_admin_group(msg: agtypes.Message, *args, **kwargs):
    """Report group ID when mentioned in admin group"""
    bot, group = msg.bot, msg.chat
    await send_new_msg_with_keyboard(bot, group.id, 'Choose:', bot.admin_menu)


@log
@handle_error
async def handle_contact(msg: agtypes.Message, state: FSMContext, *args, **kwargs) -> None:
    """Handle phone contact; lookup/update user; transition to category selection."""
    bot = msg.bot
    db = bot.db
    
    if not msg.contact or msg.contact.user_id != msg.from_user.id:
        await msg.answer("Пожалуйста, поделитесь своим номером (нажмите кнопку и выберите 'Поделиться номером телефона').")
        await state.clear()
        return

    phone = msg.contact.phone_number
    sender_id = msg.from_user.id
    redacted_phone = f"****{phone[-4:]}"
    await bot.log(f"Received contact: {redacted_phone} (user_id: {sender_id})")

    try:
        user = await db.boom_user.find_by_phone(phone)
        if user:
            await bot.log(f"User found: ID {user.id}, name {user.name}")
            if not user.telegram_id or user.telegram_id != sender_id:
                await db.boom_user.update_telegram_id(user.id, sender_id)
            greeting = "Здравствуйте, это служба заботы о клиентах Boon Market. Чтобы мы быстрее помогли, выберите тему обращения."
        else:
            await bot.log(f"User not found: {redacted_phone}")
            greeting = "Здравствуйте, это служба заботы о клиентах Boon Market. Чтобы мы быстрее помогли, выберите тему обращения."
        
        await state.set_state(SupportFlow.category)
        await msg.answer(greeting, reply_markup=get_categories_keyboard())
        
    except ValueError as e:
        await bot.log_error(e)
        await msg.answer("Неверный формат номера телефона. Попробуйте снова.")
        await state.clear()
    except Exception as e:
        await bot.log_error(e)
        await msg.answer("Временная ошибка в системе. Попробуйте позже.", reply_markup=get_remove_keyboard())
        await state.clear()


@log
@handle_error
async def handle_categories(msg: agtypes.Message, state: FSMContext, *args, **kwargs) -> None:
    """Handle category selections; fetch orders if needed, handle unregistered case."""
    bot = msg.bot
    db = bot.db
    text = msg.text
    sender_id = msg.from_user.id

    if text == "Вопрос по заказу":
        user = await db.boom_user.find_by_telegram_id(sender_id)
        cat_text = "проблемы с заказом"
        await state.update_data(category=cat_text)
        if not user:
            await state.update_data(order="не указан")
            await state.set_state(SupportFlow.description)
            await msg.answer(
                f"Вы выбрали {cat_text}, но поскольку вы не связаны с аккаунтом Boon Market, у нас нет ваших заказов. "
                "Задайте свой вопрос - мы поможем как можно скорее", 
                reply_markup=get_remove_keyboard()
            )
            return
        orders = await db.boom_user.get_recent_orders(user.id)
        await state.set_state(SupportFlow.order_select)
        full_text = f"Выберите номер заказа, по которому нужна помощь"
        if not orders:
            full_text += "\nНет недавних заказов"
        await msg.answer(full_text, reply_markup=await get_orders_keyboard(orders, cat_text, state))
        
    elif text == "Где мой заказ":
        user = await db.boom_user.find_by_telegram_id(sender_id)
        cat_text = "задержки доставки"
        await state.update_data(category=cat_text)
        if not user:
            await state.update_data(order="не указан")
            await state.set_state(SupportFlow.description)
            await msg.answer(
                f"Вы выбрали {cat_text}, но поскольку вы не связаны с аккаунтом Boon Market, у нас нет ваших заказов. "
                "Задайте свой вопрос - мы поможем как можно скорее", 
                reply_markup=get_remove_keyboard()
            )
            return
        orders = await db.boom_user.get_recent_orders(user.id)
        await state.set_state(SupportFlow.order_select)
        full_text = f"Выберите номер заказа, по которому нужна помощь"
        if not orders:
            full_text += "\nНет недавних заказов"
        await msg.answer(full_text, reply_markup=await get_orders_keyboard(orders, cat_text, state))
        
    elif text == "Другой вопрос":
        await state.update_data(category="Другой вопрос")
        await state.set_state(SupportFlow.description)
        await msg.answer("Напишите, пожалуйста, вопрос - мы поможем как можно скорее", reply_markup=get_remove_keyboard())
        
    elif text == "Частые вопросы":
        await msg.answer("Выберите вопрос:", reply_markup=get_faq_keyboard())
        
    elif text == "Назад ⏪":
        await msg.answer("Напишите, пожалуйста, вопрос - мы поможем как можно скорее:", reply_markup=get_categories_keyboard())


@log
@handle_error
async def handle_order_select(msg: agtypes.Message, state: FSMContext, *args, **kwargs) -> None:
    """Handle order selection or back/other."""
    text = msg.text
    data = await state.get_data()
    category = data.get('category', '')
    orders_map = data.get('orders_map', {})

    if text == "Другой вопрос":
        await state.update_data(order="не указан", category=category)
        await state.set_state(SupportFlow.description)
        await msg.answer("Задайте свой вопрос - мы поможем как можно скорее", reply_markup=get_remove_keyboard())
        
    elif text == "Назад ⏪":
        await msg.answer("Напишите, пожалуйста, вопрос - мы поможем как можно скорее:", reply_markup=get_categories_keyboard())
        await state.set_state(SupportFlow.category)
        
    elif text and ("Последний заказ" in text or "Заказ №" in text):
        # Достаем реальный order_number из маппинга
        order_num = orders_map.get(text)
        
        if not order_num:
            # Fallback: пытаемся извлечь из текста
            try:
                order_num = text.split("№")[1].split()[0]
            except (IndexError, AttributeError):
                await msg.answer("Не удалось определить номер заказа. Попробуйте еще раз.", 
                               reply_markup=get_categories_keyboard())
                return
        
        await state.update_data(order=order_num)
        await state.set_state(SupportFlow.description)
        await msg.answer("Задайте свой вопрос - мы поможем как можно скорее", reply_markup=get_remove_keyboard())
    else:
        await msg.answer("Неизвестная команда. Выберите из меню.", reply_markup=get_categories_keyboard())


@log
@handle_error
async def handle_faq(msg: agtypes.Message, state: FSMContext, *args, **kwargs) -> None:
    """Handle FAQ selections."""
    text = msg.text
    
    if text == "Назад ⏪":
        await msg.answer("Напишите, пожалуйста, вопрос - мы поможем как можно скорее", reply_markup=get_categories_keyboard())

    elif text == "Как узнать статус заказа":
        await msg.answer(
            "Перейдите в Ваш профиль в раздел “Мои заказы”. Откройте нужный заказ, чтобы увидеть подробности.", 
            reply_markup=get_faq_keyboard()
        )
        
    elif text == "Как сделать возврат":
        await msg.answer(
            "Обратитесь в службу заботы для оформления возврата.", 
            reply_markup=get_faq_keyboard()
        )


@log
@handle_error
async def handle_closure_confirmation(call: agtypes.CallbackQuery, *args, **kwargs):
    """Handle closure confirmation from user"""
    bot = call.message.bot
    
    try:
        # Исправлено: проверяем префикс 't:' вместо 't::'
        if not call.data.startswith('t:'):
            await call.answer("Неверный формат данных")
            return
        
        cbd = TicketCBD.unpack(call.data)
        ticket_id = cbd.ticket_id
        action = cbd.action
        
        ticket = await bot.db.tickets.get_by_id(ticket_id)
        if not ticket:
            await call.answer("Тикет не найден")
            return
        
        now_yakutsk = datetime.datetime.now(ZoneInfo("Asia/Yakutsk"))
        yakutsk_hour = now_yakutsk.hour
        
        if action == 'closure_yes':            
            # Close forum topic
            if ticket.thread_id:
                group_id = bot.cfg['admin_group_id']
                try:
                    await bot.close_forum_topic(group_id, ticket.thread_id)
                    await bot.log(f"Forum topic {ticket.thread_id} closed for ticket {ticket_id}")
                except Exception as e:
                    await bot.log_error(f"Failed to close forum topic {ticket.thread_id}: {e}")
            
            # Thank user
            await call.message.edit_text(
                f"Мы рады, что вопрос решен 😊\n"
                f"Спасибо, что обратились в службу заботы Boon Market 🩷\n\n"
                f"Пожалуйста, оцените качество обслуживания - это поможет сделать сервис лучше:",
                reply_markup=build_rating_keyboard(ticket_id).as_markup()
            )
            
            # # Notify admin
            # if ticket.thread_id:
            #     await bot.send_message(
            #         bot.cfg['admin_group_id'],
            #         f"✅ Тикет №{ticket_id} закрыт с подтверждением пользователя. Ожидается оценка ⭐.",
            #         message_thread_id=ticket.thread_id
            #     )
            
            await call.answer("Обращение закрыто, ждём оценку!")
            
        elif action == 'closure_no':
            # User says issue NOT resolved - reopen ticket
            await bot.db.tickets.update_status(ticket_id, 'reopened')
            
            # Reopen forum topic
            if ticket.thread_id:
                group_id = bot.cfg['admin_group_id']
                try:
                    await bot.reopen_forum_topic(group_id, ticket.thread_id)
                    await bot.log(f"Forum topic {ticket.thread_id} reopened for ticket {ticket_id}")
                except Exception as e:
                    await bot.log_error(f"Failed to reopen forum topic {ticket.thread_id}: {e}")
            
            # Ask user to clarify
            if 8 <= yakutsk_hour <= 23:
                response = (
                    f"Пожалуйста, уточните, что именно осталось не решенным - оператор скоро с Вами свяжется 🙌, "
                )
            else:
                response = (
                    f"Пожалуйста, уточните, что именно осталось не решенным - оператор скоро с Вами свяжется 🙌. График работы техподдержки: с 08:00 до 23:00. "
                )
            
            await call.message.edit_text(response)
            
            # Notify admin about reopening
            if ticket.thread_id:
                await bot.send_message(
                    bot.cfg['admin_group_id'],
                    f"🔄 Пользователь указал, что вопрос не решен. Ожидается уточнение",
                    message_thread_id=ticket.thread_id
                )
            
            await call.answer("Обращение осталось открытым")
        
    except Exception as e:
        await bot.log_error(e)
        await call.answer("Произошла ошибка")

@log
@handle_error
async def handle_start_over(call: agtypes.CallbackQuery, state: FSMContext, *args, **kwargs):
    """Handle 'новое обращение' button"""
    await call.answer()
    old_text = call.message.text or ""
    if "Чтобы начать" in old_text:
        old_text = old_text.split("Чтобы начать")[0].strip()

    await call.message.edit_text(f"{old_text}\n\nОбращение открыто заново 💬")


    # эмулируем /start
    await cmd_start(call.message, state, user_id=call.from_user.id)

@log
@handle_error
async def handle_rating(call: agtypes.CallbackQuery, *args, **kwargs):
    """Handle user rating (1–5 stars)"""
    bot = call.message.bot
    try:
        _, ticket_id, rating_str = call.data.split(':')
        rating = int(rating_str)

        # Обновляем рейтинг в базе
        await bot.db.tickets.update_rating(ticket_id, rating)

        # Обновляем сообщение
        await call.message.edit_text(
            f"Спасибо за оценку {rating}⭐!\n"
            f"Чтобы начать новое обращение — нажмите кнопку ниже 👇",
            reply_markup=build_start_over_keyboard().as_markup()
        )

        # Уведомляем админов
        ticket = await bot.db.tickets.get_by_id(ticket_id)
        if ticket and ticket.thread_id:
            stars = "⭐" * rating
            await bot.send_message(
                bot.cfg['admin_group_id'],
                f"✅ Тикет закрыт. Пользователь поставил оценку: {stars}\n",
                message_thread_id=ticket.thread_id
            )

        await call.answer("Спасибо за вашу оценку!")
    except Exception as e:
        await bot.log_error(e)
        await call.answer("Произошла ошибка при сохранении оценки.")



def register_handlers(dp: Dispatcher) -> None:
    """Register all the handlers to the provided dispatcher"""
    # Basic commands
    dp.message.register(cmd_start, PrivateChatFilter(), Command('start'))
    dp.callback_query.register(handle_start_over, BtnInPrivateChat(), F.data == "start_over")
    dp.message.register(added_to_group, NewChatMembersFilter())
    dp.message.register(group_chat_created, GroupChatCreatedFilter())
    dp.message.register(mention_in_admin_group, BotMention(), InAdminGroup())
    
    # Admin commands
    dp.message.register(cmd_close_ticket, InAdminGroup(), Command('close'))
    dp.message.register(admin_broadcast_ask_confirm, BroadcastForm.message)
    dp.callback_query.register(admin_broadcast_finish, BroadcastForm.confirm, BtnInAdminGroup())

    # SUPPORT FLOW HANDLERS (specific > general)
    dp.message.register(handle_contact, PrivateChatFilter(), F.contact)
    
    # Category handlers
    dp.message.register(
        handle_categories, 
        SupportFlow.category, 
        F.text.in_([
            "Вопрос по заказу", 
            "Где мой заказ", 
            "Другой вопрос", 
            "Частые вопросы",
            "Назад ⏪"
        ])
    )
    
    # Order selection
    dp.message.register(handle_order_select, SupportFlow.order_select)
    
    # FAQ handlers
    dp.message.register(
        handle_faq, 
        SupportFlow.category, 
        F.text.in_([
            "Как узнать статус заказа", 
            "Как сделать возврат", 
            "Назад ⏪"
        ])
    )

    # RATING HANDLER (исправлено: проверяем префикс 't:' вместо 't::')
    dp.callback_query.register(handle_closure_confirmation, BtnInPrivateChat(), F.data.startswith('t:'))
    dp.callback_query.register(handle_rating, BtnInPrivateChat(), F.data.startswith('rate:'))

    # GENERAL CALLBACK HANDLERS
    dp.callback_query.register(user_btn_handler, BtnInPrivateChat())
    dp.callback_query.register(admin_btn_handler, BtnInAdminGroup())

    # GENERAL MESSAGE HANDLERS (fallback)
    dp.message.register(user_message, PrivateChatFilter(), ~ACommandFilter())
    dp.message.register(admin_message, ~ACommandFilter(), ReplyToBotInGroupForwardedFilter())