import os
import logging
import asyncio
import random
import uuid
from typing import Dict, List, Set
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ===== НАСТРОЙКИ =====
BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_IDS = set(map(int, os.getenv('ADMIN_IDS', '').split(','))) if os.getenv('ADMIN_IDS') else set()

# ===== СОСТОЯНИЯ FSM =====
class ContestStates(StatesGroup):
    waiting_for_conditions = State()
    waiting_for_subscription = State()
    waiting_for_channels = State()
    waiting_for_winner_count = State()
    waiting_for_target_channel = State()
    waiting_for_winners = State()
    waiting_for_results_link = State()

# ===== ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ =====
contests: Dict[str, Dict] = {}
participants: Dict[str, List[Dict]] = {}
results_links: Dict[str, str] = {}
unique_users: Set[int] = set()

# ===== ИНИЦИАЛИЗАЦИЯ =====
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ===== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def generate_contest_id(is_fast: bool = False) -> str:
    while True:
        contest_id = f"F{random.randint(100000, 999999)}" if is_fast else str(random.randint(100000, 999999))
        if contest_id not in contests:
            return contest_id

def get_statistics() -> str:
    total_contests = len(contests)
    total_participants = sum(len(participants.get(cid, [])) for cid in contests)
    total_users = len(unique_users)
    return (
        f"📊 Статистика бота:\n"
        f"Всего конкурсов: {total_contests}\n"
        f"Всего участников: {total_participants}\n"
        f"Уникальных пользователей: {total_users}"
    )

# ===== ОБРАБОТЧИКИ КОМАНД =====
@dp.message(CommandStart())
async def cmd_start(message: Message):
    user_id = message.from_user.id
    kb = InlineKeyboardBuilder()
    
    kb.button(text="🎉 Создать конкурс", callback_data="new_contest")
    
    active_contests = [cid for cid, c in contests.items() 
                      if c['is_active'] and c['creator_id'] == user_id]
    if active_contests:
        kb.button(text="🏆 Подвести итоги", callback_data="pick_winners")
    
    finished_contests = [cid for cid, c in contests.items() 
                        if not c['is_active'] and c['creator_id'] == user_id]
    if finished_contests:
        kb.button(text="🔄 Рерол победителей", callback_data="reroll_winners")
    
    if is_admin(user_id):
        kb.button(text="📊 Статистика", callback_data="stats")
    
    kb.adjust(1)
    
    await message.answer(
        "👋 Привет! Я бот для проведения конкурсов в Telegram.\n\n"
        "Выберите действие:",
        reply_markup=kb.as_markup()
    )

@dp.callback_query(lambda c: c.data == "new_contest")
async def new_contest(call: CallbackQuery, state: FSMContext):
    if call.message.chat.type != "private":
        await call.message.answer("❌ Создавайте конкурсы в личных сообщениях с ботом")
        return

    await call.message.answer(
        "📝 Отправьте текст конкурса одним сообщением.\n\n"
        "Пример:\n"
        "«Конкурс на лучший комментарий! Приз - 1000 рублей. "
        "Конкурс продлится до 31 декабря.»"
    )
    await state.set_state(ContestStates.waiting_for_conditions)
    await call.message.delete()

@dp.message(ContestStates.waiting_for_conditions)
async def conditions_received(message: Message, state: FSMContext):
    await state.update_data(conditions=message.text)
    await message.answer(
        "📋 Укажите условия подписки (например: Подпишитесь на @channel1 и @channel2, чтобы участвовать)"
    )
    await state.set_state(ContestStates.waiting_for_subscription)

@dp.message(ContestStates.waiting_for_subscription)
async def subscription_received(message: Message, state: FSMContext):
    await state.update_data(subscription_conditions=message.text)
    await message.answer("🔗 Отправьте username каналов через запятую (@channel1, @channel2)")
    await state.set_state(ContestStates.waiting_for_channels)

@dp.message(ContestStates.waiting_for_channels)
async def channels_received(message: Message, state: FSMContext):
    channels = [ch.strip().replace('@', '') for ch in message.text.split(',') if ch.strip()]
    if not channels:
        await message.answer("❌ Неверный формат. Попробуйте еще раз.")
        return

    await state.update_data(channels=channels)
    await message.answer("🔢 Укажите количество победителей:")
    await state.set_state(ContestStates.waiting_for_winner_count)

@dp.message(ContestStates.waiting_for_winner_count)
async def winners_received(message: Message, state: FSMContext):
    try:
        count = int(message.text)
        if count <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите положительное число.")
        return

    data = await state.get_data()
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Подтвердить", callback_data=f"confirm:{count}")
    kb.button(text="❌ Отменить", callback_data="cancel")
    kb.adjust(1)

    await message.answer(
        f"📋 Проверьте данные конкурса:\n\n"
        f"📌 Текст: {data['conditions']}\n"
        f"📋 Условия подписки: {data['subscription_conditions']}\n"
        f"📢 Каналы: {', '.join(f'@{ch}' for ch in data['channels'])}\n"
        f"🏆 Победителей: {count}",
        reply_markup=kb.as_markup()
    )
    await state.update_data(winner_count=count)

@dp.callback_query(lambda c: c.data.startswith("confirm"))
async def confirm_callback(call: CallbackQuery, state: FSMContext):
    await call.message.edit_reply_markup()
    await call.message.answer(
        "📢 Укажите username канала для публикации (например @my_channel)"
    )
    await state.set_state(ContestStates.waiting_for_target_channel)

@dp.callback_query(lambda c: c.data == "cancel")
async def cancel_callback(call: CallbackQuery, state: FSMContext):
    await call.message.edit_reply_markup()
    await call.message.answer("❌ Создание конкурса отменено.")
    await state.clear()

@dp.message(ContestStates.waiting_for_target_channel)
async def publish_contest(message: Message, state: FSMContext):
    user_id = message.from_user.id
    target = message.text.replace("@", "").strip()
    data = await state.get_data()

    try:
        chat = await bot.get_chat(f"@{target}")
        member = await bot.get_chat_member(chat.id, user_id)
        if member.status not in ["administrator", "creator"]:
            await message.answer(
                "⚠️ Ошибка\n"
                "❌ Вы не являетесь администратором канала\n"
                "Обратитесь к администратору - @icaacull"
            )
            return

        contest_id = generate_contest_id(is_fast=False)
        text = (
            f"🎉 КОНКУРС 🎉\n\n"
            f"Условия: {data['conditions']}\n\n"
            f"Подписаться на: {', '.join(f'@{ch}' for ch in data['channels'])}\n\n"
            f"Победителей: {data['winner_count']}"
        )

        btn = InlineKeyboardButton(text="🎁 Участвовать", callback_data=f"join:{contest_id}")
        msg = await bot.send_message(chat.id, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[btn]]))

        contests[contest_id] = {
            **data,
            'channel_id': chat.id,
            'message_id': msg.message_id,
            'creator_id': user_id,
            'is_active': True,
            'is_fast': False
        }
        participants[contest_id] = []

        # Notify admins about the new regular contest
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    admin_id,
                    f"🆕 Новый конкурс создан (ID: {contest_id})\n"
                    f"Канал: @{target}\n"
                    f"Условия: {data['conditions']}\n"
                    f"Подписаться на: {', '.join(f'@{ch}' for ch in data['channels'])}\n"
                    f"Победителей: {data['winner_count']}"
                )
            except Exception as e:
                logging.error(f"Ошибка при уведомлении админа {admin_id}: {e}")

        await message.answer(
            f"✅ Конкурс опубликован в @{target}!\n"
            f"ID конкурса: {contest_id}\n"
            f"Используйте: @giveawaygasbot {contest_id} в inline-режиме для доступа к конкурсу."
        )
    except Exception as e:
        logging.error(f"Ошибка в publish_contest: {e}")
        await message.answer(f"❌ Ошибка публикации: {e}")
    finally:
        await state.clear()

@dp.callback_query(lambda c: c.data.startswith("join"))
async def join_contest(call: CallbackQuery):
    user = call.from_user
    contest_id = call.data.split(":")[1]
    contest = contests.get(contest_id)

    if not contest or not contest['is_active']:
        await bot.answer_callback_query(call.id, "⚠️ Конкурс не найден или завершён", show_alert=True)
        return

    unique_users.add(user.id)

    if any(p['user_id'] == user.id for p in participants.get(contest_id, [])):
        await bot.answer_callback_query(call.id, "Ты уже участвуешь!", show_alert=True)
        return

    not_subbed = []
    if contest.get('is_fast', False):
        try:
            chat = await bot.get_chat(contest['channel_id'])
            member = await bot.get_chat_member(contest['channel_id'], user.id)
            if member.status not in ["member", "administrator", "creator"]:
                not_subbed.append(chat.username or f"channel_{contest['channel_id']}")
        except Exception:
            not_subbed.append(chat.username or f"channel_{contest['channel_id']}")
    else:
        for ch in contest.get('channels', []):
            try:
                chat = await bot.get_chat(f"@{ch}")
                member = await bot.get_chat_member(chat.id, user.id)
                if member.status not in ["member", "administrator", "creator"]:
                    not_subbed.append(ch)
            except Exception:
                not_subbed.append(ch)

    if not_subbed:
        alert_text = "Проверь подписку!\n" + "\n".join(f"Ты не подписан на @{ch}" for ch in not_subbed)
        await bot.answer_callback_query(call.id, alert_text, show_alert=True)
        return

    participants.setdefault(contest_id, []).append({
        'user_id': user.id,
        'username': user.username if user.username else None,
        'name': user.full_name
    })

    await bot.answer_callback_query(call.id, "Ты успешно участвуешь!", show_alert=True)

@dp.callback_query(lambda c: c.data == "stats")
async def show_stats(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.message.answer("❌ Доступ только для админов.")
        return
    await call.message.answer(get_statistics())
    await call.message.delete()

@dp.callback_query(lambda c: c.data == "pick_winners")
async def pick_winners(call: CallbackQuery, state: FSMContext):
    user_id = call.from_user.id
    if call.message.chat.type != "private":
        await call.message.answer("ℹ️ Используйте эту команду в личных сообщениях с ботом.")
        return

    active_contests = [(cid, c) for cid, c in contests.items() 
                      if c['is_active'] and c['creator_id'] == user_id]
    
    if not active_contests:
        await call.message.answer("❌ Нет активных конкурсов, созданных вами.")
        await call.message.delete()
        return

    kb = InlineKeyboardBuilder()
    for contest_id, contest in active_contests:
        try:
            chat = await bot.get_chat(contest['channel_id'])
            kb.button(text=f"{'ФАСТ Конкурс' if contest.get('is_fast', False) else 'Конкурс'} в @{chat.username} (ID: {contest_id})", callback_data=f"pick:{contest_id}")
        except Exception as e:
            logging.error(f"Ошибка при получении чата в pick_winners: {e}")
    kb.button(text="Отмена", callback_data="cancel_pick")
    kb.adjust(1)

    await call.message.answer("📋 Выберите конкурс для подведения итогов:", reply_markup=kb.as_markup())
    await call.message.delete()

@dp.callback_query(lambda c: c.data == "reroll_winners")
async def reroll_winners(call: CallbackQuery, state: FSMContext):
    user_id = call.from_user.id
    if call.message.chat.type != "private":
        await call.message.answer("ℹ️ Используйте эту команду в личных сообщениях с ботом.")
        return

    finished_contests = [(cid, c) for cid, c in contests.items() 
                        if not c['is_active'] and c['creator_id'] == user_id]
    
    if not finished_contests:
        await call.message.answer("❌ Нет завершенных конкурсов, созданных вами.")
        await call.message.delete()
        return

    kb = InlineKeyboardBuilder()
    for contest_id, contest in finished_contests:
        try:
            chat = await bot.get_chat(contest['channel_id'])
            kb.button(text=f"{'ФАСТ Конкурс' if contest.get('is_fast', False) else 'Конкурс'} в @{chat.username} (ID: {contest_id})", callback_data=f"reroll:{contest_id}")
        except Exception as e:
            logging.error(f"Ошибка при получении чата в reroll_winners: {e}")
    kb.button(text="Отмена", callback_data="cancel_reroll")
    kb.adjust(1)

    await call.message.answer("📋 Выберите конкурс для пересмотра победителей:", reply_markup=kb.as_markup())
    await call.message.delete()

@dp.callback_query(lambda c: c.data.startswith("pick:") or c.data.startswith("reroll:"))
async def select_contest_for_winners(call: CallbackQuery, state: FSMContext):
    user_id = call.from_user.id
    contest_id = call.data.split(":")[1]
    contest = contests.get(contest_id)

    if not contest:
        await call.message.answer("❌ Конкурс не найден.")
        await call.message.edit_reply_markup()
        return

    if contest['creator_id'] != user_id:
        await call.message.answer("❌ Вы не являетесь создателем этого конкурса.")
        await call.message.edit_reply_markup()
        return

    users = participants.get(contest_id, [])
    if not users:
        await call.message.answer("😢 Нет участников для выбора победителей.")
        await call.message.edit_reply_markup()
        return

    try:
        if is_admin(user_id):
            text = "📋 Участники конкурса (для справки):\n\n" + (
                "\n".join(
                    f"{i+1}. @{u['username']} ({u['user_id']})" if u['username'] 
                    else f"{i+1}. {u['name']} ({u['user_id']})"
                    for i, u in enumerate(users)
                ) if users else "😢 Нет участников."
            )
            await call.message.answer(
                f"{text}\n\n"
                f"🔢 Укажите Telegram usernames или IDs победителей через запятую (например: @username1, @username2 или 123456789, 987654321):"
            )
            await state.update_data(contest_id=contest_id, is_reroll=call.data.startswith("reroll:"))
            await state.set_state(ContestStates.waiting_for_winners)
        else:
            await call.message.answer(
                "⚠️ Ошибка\n"
                "❌ Вы не являетесь администратором\n"
                "Обратитесь к администратору - @icaacull"
            )
            await call.message.edit_reply_markup()
            return
        await call.message.edit_reply_markup()
    except Exception as e:
        logging.error(f"Ошибка в select_contest_for_winners: {e}")
        await call.message.answer(f"❌ Ошибка при выборе победителей: {e}")
        await call.message.edit_reply_markup()

@dp.message(ContestStates.waiting_for_winners)
async def winners_selected(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if not is_admin(user_id):
        await message.answer(
            "⚠️ Ошибка\n"
            "❌ Вы не являетесь администратором\n"
            "Обратитесь к администратору - @icaacull"
        )
        await state.clear()
        return

    data = await state.get_data()
    contest_id = data['contest_id']
    contest = contests.get(contest_id)

    if not contest:
        await message.answer("❌ Конкурс не найден.")
        await state.clear()
        return

    if contest['creator_id'] != user_id:
        await message.answer("❌ Вы не являетесь создателем этого конкурса.")
        await state.clear()
        return

    try:
        winner_inputs = [i.strip() for i in message.text.split(',') if i.strip()]
        count = min(contest['winner_count'], len(winner_inputs))
        if len(winner_inputs) != count:
            await message.answer(f"❌ Укажите ровно {count} победителей.")
            return

        winners = []
        for input_id in winner_inputs:
            user_id_input = input_id.replace('@', '') if input_id.startswith('@') else input_id
            try:
                user_id_input = int(user_id_input)
                try:
                    user = await bot.get_chat(user_id_input)
                    winners.append({
                        'user_id': user_id_input,
                        'username': user.username if user.username else None,
                        'name': user.full_name
                    })
                except:
                    winners.append({
                        'user_id': user_id_input,
                        'username': None,
                        'name': f"User {user_id_input}"
                    })
            except ValueError:
                winners.append({
                    'user_id': None,
                    'username': user_id_input,
                    'name': user_id_input
                })

        winners_text = "🏆 Победители конкурса: " + ", ".join(
            f"@{w['username']}" if w['username'] else w['name']
            for w in winners
        )

        await message.answer(
            f"📋 Вы выбрали победителями:\n\n{winners_text}\n\n"
            f"🔗 Отправьте ссылку для кнопки 'Проверить результаты' (или 'нет', если ссылки нет):"
        )
        await state.update_data(winners_text=winners_text, winners=winners)
        await state.set_state(ContestStates.waiting_for_results_link)
    except Exception as e:
        logging.error(f"Ошибка в winners_selected: {e}")
        await message.answer(f"❌ Ошибка: {e}. Укажите usernames или IDs через запятую.")
        return

@dp.message(ContestStates.waiting_for_results_link)
async def publish_results(message: Message, state: FSMContext):
    user_id = message.from_user.id
    data = await state.get_data()
    contest_id = data['contest_id']
    contest = contests.get(contest_id)

    if not contest:
        await message.answer("❌ Конкурс не найден.")
        await state.clear()
        return

    if contest['creator_id'] != user_id:
        await message.answer("❌ Вы не являетесь создателем этого конкурса.")
        await state.clear()
        return

    if not is_admin(user_id):
        await message.answer(
            "⚠️ Ошибка\n"
            "❌ Вы не являетесь администратором\n"
            "Обратитесь к администратору - @icaacull"
        )
        await state.clear()
        return

    results_link = message.text.strip() if message.text.lower() != 'нет' else None
    if results_link and not (results_link.startswith("http://") or results_link.startswith("https://")):
        await message.answer("❌ Ссылка должна начинаться с http:// или https://")
        return

    try:
        winners_text = data['winners_text']
        if contest.get('is_fast', False):
            updated_text = f"{contest['conditions']}\n\n{winners_text}"
        else:
            updated_text = (
                f"🎉 КОНКУРС 🎉\n\n"
                f"Условия: {contest['conditions']}\n\n"
                f"Подписаться на: {', '.join(f'@{ch}' for ch in contest['channels'])}\n\n"
                f"Победителей: {contest['winner_count']}\n\n"
                f"{winners_text}"
            )

        kb = InlineKeyboardBuilder()
        if results_link:
            results_links[contest_id] = results_link
            kb.button(text="🔍 Проверить результаты", url=results_link)
        kb.adjust(1)

        await bot.edit_message_text(
            chat_id=contest['channel_id'],
            message_id=contest['message_id'],
            text=updated_text,
            reply_markup=kb.as_markup() if results_link else None
        )

        contests[contest_id]['is_active'] = False
        await message.answer("✅ Итоги конкурса опубликованы в исходном посте!")
    except Exception as e:
        logging.error(f"Ошибка в publish_results: {e}")
        await message.answer(f"❌ Ошибка публикации итогов: {e}")
    finally:
        await state.clear()

@dp.callback_query(lambda c: c.data == "cancel_pick")
async def cancel_pick_callback(call: CallbackQuery, state: FSMContext):
    await call.message.edit_reply_markup()
    await call.message.answer("❌ Подведение итогов отменено.")
    await state.clear()

@dp.callback_query(lambda c: c.data == "cancel_reroll")
async def cancel_reroll_callback(call: CallbackQuery, state: FSMContext):
    await call.message.edit_reply_markup()
    await call.message.answer("❌ Пересмотр победителей отменен.")
    await state.clear()

@dp.inline_query()
async def inline_query_handler(query: InlineQuery):
    query_text = query.query.strip()
    user_id = query.from_user.id

    if query_text.startswith("conc "):
        try:
            parts = query_text[5:].split()
            if len(parts) < 3:
                await query.answer(
                    results=[
                        InlineQueryResultArticle(
                            id=str(uuid.uuid4()),
                            title="Неверный формат",
                            input_message_content=InputTextMessageContent(
                                message_text="❌ Укажите описание, количество участников, время и (опционально) канал.\nПример: @giveawaygasbot conc Тест 3 5 @MyChannel"
                            ),
                            description="Формат: <описание> <участники> <минуты> [@канал]"
                        )
                    ],
                    cache_time=1
                )
                return

            potential_numbers = [p for p in reversed(parts) if not p.startswith('@')]
            if len(potential_numbers) < 2:
                await query.answer(
                    results=[
                        InlineQueryResultArticle(
                            id=str(uuid.uuid4()),
                            title="Ошибка",
                            input_message_content=InputTextMessageContent(
                                message_text="❌ Укажите количество участников и время (числа) перед каналом, если он указан."
                            ),
                            description="Формат: <описание> <участники> <минуты> [@канал]"
                        )
                    ],
                    cache_time=1
                )
                return

            minutes = int(potential_numbers[0])
            winner_count = int(potential_numbers[1])
            if winner_count <= 0 or minutes <= 0:
                raise ValueError("Количество участников и время должны быть больше 0")

            description_parts = [p for p in parts if not p.startswith('@') and not p.isdigit()]
            description = " ".join(description_parts)

            target_channel = next((p.replace('@', '').strip() for p in parts if p.startswith('@')), None)

            if not target_channel:
                await query.answer(
                    results=[
                        InlineQueryResultArticle(
                            id=str(uuid.uuid4()),
                            title="Ошибка",
                            input_message_content=InputTextMessageContent(
                                message_text="❌ Укажите канал (например @MyChannel)."
                            ),
                            description="Формат: <описание> <участники> <минуты> [@канал]"
                        )
                    ],
                    cache_time=1
                )
                return

            try:
                chat = await bot.get_chat(f"@{target_channel}")
                member = await bot.get_chat_member(chat.id, user_id)
                if member.status not in ["administrator", "creator"]:
                    await query.answer(
                        results=[
                            InlineQueryResultArticle(
                                id=str(uuid.uuid4()),
                                title="Ошибка",
                                input_message_content=InputTextMessageContent(
                                    message_text=
                                    "⚠️ Ошибка\n"
                                    "❌ Вы не являетесь администратором канала\n"
                                    "Обратитесь к администратору - @icaacull"
                                ),
                                description="Только администраторы могут создавать конкурсы."
                            )
                        ],
                        cache_time=1
                    )
                    return

                # Публикуем конкурс сразу
                contest_id = generate_contest_id(is_fast=True)
                text = (
                    f"🎉 ФАСТ КОНКУРС 🎉\n\n"
                    f"Условия: {description}\n\n"
                    f"Подписаться на: @{target_channel}\n\n"
                    f"Победителей: {winner_count}\n\n"
                    f"Длительность: {minutes} минут"
                )

                btn = InlineKeyboardButton(text="🎁 Участвовать", callback_data=f"join:{contest_id}")
                msg = await bot.send_message(chat.id, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[btn]]))

                contests[contest_id] = {
                    'conditions': text,
                    'winner_count': winner_count,
                    'channel_id': chat.id,
                    'message_id': msg.message_id,
                    'creator_id': user_id,
                    'is_active': True,
                    'is_fast': True,
                    'duration_minutes': minutes,
                    'channels': [target_channel]
                }
                participants[contest_id] = []

                # Notify admins about the new fast contest
                for admin_id in ADMIN_IDS:
                    try:
                        await bot.send_message(
                            admin_id,
                            f"🆕 Новый ФАСТ конкурс создан (ID: {contest_id})\n"
                            f"Канал: @{target_channel}\n"
                            f"Условия: {description}\n"
                            f"Подписаться на: @{target_channel}\n"
                            f"Победителей: {winner_count}\n"
                            f"Длительность: {minutes} минут"
                        )
                    except Exception as e:
                        logging.error(f"Ошибка при уведомлении админа {admin_id}: {e}")

                # Notify creator in private messages
                await bot.send_message(
                    user_id,
                    f"✅ ФАСТ Конкурс опубликован в @{target_channel}!\nID: {contest_id}\n"
                    f"Используйте: @giveawaygasbot {contest_id} в inline-режиме для доступа к конкурсу."
                )

                # Show preview in inline query results
                kb = InlineKeyboardBuilder()
                kb.button(text="🎁 Участвовать", callback_data=f"join:{contest_id}")
                kb.adjust(1)

                await query.answer(
                    results=[
                        InlineQueryResultArticle(
                            id=str(uuid.uuid4()),
                            title=f"ФАСТ Конкурс в @{target_channel} (ID: {contest_id})",
                            input_message_content=InputTextMessageContent(
                                message_text=text
                            ),
                            description=description[:100] + ("..." if len(description) > 100 else ""),
                            reply_markup=kb.as_markup()
                        )
                    ],
                    cache_time=1
                )
            except Exception as e:
                logging.error(f"Ошибка при создании ФАСТ конкурса: {e}")
                await query.answer(
                    results=[
                        InlineQueryResultArticle(
                            id=str(uuid.uuid4()),
                            title="Ошибка",
                            input_message_content=InputTextMessageContent(
                                message_text=f"❌ Ошибка публикации: {e}\nПопробуйте создать конкурс через /start."
                            ),
                            description="Убедитесь, что канал существует и бот добавлен."
                        )
                    ],
                    cache_time=1
                )
                return
        except ValueError as e:
            logging.error(f"Ошибка при создании ФАСТ конкурса: {e}")
            await query.answer(
                results=[
                    InlineQueryResultArticle(
                        id=str(uuid.uuid4()),
                        title="Ошибка",
                        input_message_content=InputTextMessageContent(
                            message_text=f"❌ {e}\nФормат: @giveawaygasbot conc <описание> <участники> <минуты> [@канал]"
                        ),
                        description="Проверьте формат запроса."
                    )
                ],
                cache_time=1
            )
        return
    elif query_text.lower() == "concu":
        await query.answer(
            results=[
                InlineQueryResultArticle(
                    id=str(uuid.uuid4()),
                    title="Неверная команда",
                    input_message_content=InputTextMessageContent(
                        message_text="❌ Возможно, вы имели в виду @giveawaygasbot conc <описание> <участники> <минуты> [@канал]\nПример: @giveawaygasbot conc Тест 3 5 @MyChannel"
                    ),
                    description="Используйте 'conc' для создания ФАСТ конкурса."
                )
            ],
            cache_time=1
        )
        return

    contest_id = query_text
    if not contest_id:
        await query.answer(
            results=[
                InlineQueryResultArticle(
                    id=str(uuid.uuid4()),
                    title="Введите 6-значный ID конкурса",
                    input_message_content=InputTextMessageContent(
                        message_text="Введите 6-значный ID конкурса, например: @giveawaygasbot 123456\nИли создайте ФАСТ конкурс: @giveawaygasbot conc <описание> <участники> <минуты> [@канал]"
                    ),
                    description="Укажите ID конкурса или используйте 'conc' для создания конкурса."
                )
            ],
            cache_time=1
        )
        return

    if not (contest_id.isdigit() or (contest_id.startswith('F') and contest_id[1:].isdigit())) or len(contest_id) != (7 if contest_id.startswith('F') else 6):
        await query.answer(
            results=[
                InlineQueryResultArticle(
                    id=str(uuid.uuid4()),
                    title="Неверный формат команды или ID",
                    input_message_content=InputTextMessageContent(
                        message_text="❌ Введите 6-значный ID конкурса (например: @giveawaygasbot 123456) или используйте @giveawaygasbot conc <описание> <участники> <минуты> [@канал]\nПример: @giveawaygasbot conc Тест 3 5 @MyChannel"
                    ),
                    description="Проверьте ID или используйте 'conc' для создания конкурса."
                )
            ],
            cache_time=1
        )
        return

    contest = contests.get(contest_id)
    if not contest:
        await query.answer(
            results=[
                InlineQueryResultArticle(
                    id=str(uuid.uuid4()),
                    title="Конкурс не найден",
                    input_message_content=InputTextMessageContent(
                        message_text=f"❌ Конкурс с ID {contest_id} не найден.\nИли создайте ФАСТ конкурс: @giveawaygasbot conc <описание> <участники> <минуты> [@канал]"
                    ),
                    description="Проверьте правильность ID конкурса или создайте новый конкурс."
                )
            ],
            cache_time=1
        )
        return

    try:
        chat = await bot.get_chat(contest['channel_id'])
        text = contest['conditions']
        kb = InlineKeyboardBuilder()
        if contest['is_active']:
            kb.button(text="🎁 Участвовать", callback_data=f"join:{contest_id}")
        kb.adjust(1)

        await query.answer(
            results=[
                InlineQueryResultArticle(
                    id=str(uuid.uuid4()),
                    title=f"{'ФАСТ Конкурс' if contest.get('is_fast', False) else 'Конкурс'} в @{chat.username} (ID: {contest_id})",
                    input_message_content=InputTextMessageContent(
                        message_text=text
                    ),
                    description=contest['conditions'][:100] + ("..." if len(contest['conditions']) > 100 else ""),
                    reply_markup=kb.as_markup() if contest['is_active'] else None
                )
            ],
            cache_time=1
        )
    except Exception as e:
        logging.error(f"Ошибка в inline_query_handler: {e}")
        await query.answer(
            results=[
                InlineQueryResultArticle(
                    id=str(uuid.uuid4()),
                    title="Ошибка",
                    input_message_content=InputTextMessageContent(
                        message_text=f"❌ Ошибка при получении конкурса: {e}\nИли создайте ФАСТ конкурс: @giveawaygasbot conc <описание> <участники> <минуты> [@канал]"
                    ),
                    description="Попробуйте снова или создайте новый конкурс."
                )
            ],
            cache_time=1
        )

async def handle(request):
    return web.Response(text="Bot is running")

async def start_server():
    app = web.Application()
    app.router.add_get('/', handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    await site.start()
    logging.info("HTTP server started on port 8080")

# ===== ЗАПУСК БОТА =====
async def main():
    await asyncio.gather(
        dp.start_polling(bot),
        start_server()
    )

if __name__ == "__main__":
    try:
        logging.info("Starting bot...")
        asyncio.run(main())
    except Exception as e:
        logging.error(f"Bot crashed: {e}")
    finally:
        logging.info("Bot stopped")
