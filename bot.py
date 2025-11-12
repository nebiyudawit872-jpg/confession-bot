import os
import asyncio
import random
import time
from datetime import datetime, UTC, timedelta
from dotenv import load_dotenv
import re

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandObject 
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message, FSInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError, TelegramNotFound 

from pymongo import MongoClient
from bson import ObjectId

# -------------------------
# Load env (BOT_TOKEN, MONGO_URI, BOT_USERNAME)
# -------------------------
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")

# --- CRITICAL CONFIG ---
# NOTE: This value is CRITICAL for the link button to work correctly.
BOT_USERNAME = os.getenv("BOT_USERNAME", "UoG_confessions_bot").lstrip('@').lower() 

if not BOT_TOKEN or not MONGO_URI:
    raise RuntimeError("Please set BOT_TOKEN and MONGO_URI in your .env file")

# ------------------------
# Config (CRUCIAL: CHECK THESE IDs)
# -------------------------
# Admin ID (Current: 905781541, 1079361596) - REPLACE WITH YOUR ADMIN IDs
ADMIN_IDS = [905781541, 1079361596] 
CHANNEL_ID = int(os.getenv("CHANNEL_ID", -1001234567890)) # Replace with your channel ID
LOG_CHAT_ID = int(os.getenv("LOG_CHAT_ID", 905781541)) # Log chat ID for mod/admin messages

# --- AURA POINT CONFIGURATION ---
AURA_POINT_CONFESSION = 10
AURA_POINT_COMMENT = 5
AURA_POINT_LIMIT_DAILY = 50 # Max points a user can earn in 24 hours
TIME_ZONE_OFFSET = timedelta(hours=3) # EAT is UTC+3 (Adjust this if your timezone is different)

# -------------------------
# MongoDB Setup
# -------------------------
client = MongoClient(MONGO_URI)
db = client.get_database("confessions_db")
conf_col = db.get_collection("confessions")
comments_col = db.get_collection("comments")
users_col = db.get_collection("users") # New collection for user data/points
bans_col = db.get_collection("bans") # For storing permanent ban information

# -------------------------
# FSM States
# -------------------------
class ConfessionForm(StatesGroup):
    waiting_for_text = State()
    waiting_for_photo = State()

class CommentForm(StatesGroup):
    waiting_for_text = State()
    conf_id = State()

class AdminForm(StatesGroup):
    waiting_for_anon_id = State()
    waiting_for_reason = State()

# -------------------------
# Utility Functions
# -------------------------

def is_admin(user_id):
    return user_id in ADMIN_IDS

def get_next_confession_number():
    """Atomically increments the confession counter."""
    result = db.counters.find_one_and_update(
        {"_id": "confession_num"},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=True
    )
    return result["seq"]

def get_last_confession_number():
    """Gets the last used confession number."""
    result = db.counters.find_one({"_id": "confession_num"})
    return result["seq"] if result else 0

async def check_user_ban(user_id: int):
    """Checks if a user is globally banned."""
    # Check the users_col flag for quick check
    user_doc = users_col.find_one({"_id": user_id})
    if user_doc and user_doc.get("is_banned", False):
        return True
    
    # Check the dedicated bans collection (optional but good for tracking)
    ban_doc = bans_col.find_one({"_id": user_id})
    if ban_doc:
        # If found in bans but not flagged in users_col (inconsistent state), fix it
        users_col.update_one({"_id": user_id}, {"$set": {"is_banned": True}}, upsert=True)
        return True
    return False

# -------------------------
# Aura Point Helpers
# -------------------------

def get_current_time_utc_plus(offset: timedelta) -> datetime:
    """Returns the current time adjusted for the configured timezone."""
    return datetime.now(UTC) + offset

def get_aura_point_log_key():
    """Generates the daily log key based on the timezone offset."""
    today = get_current_time_utc_plus(TIME_ZONE_OFFSET).date()
    return f"log_{today.isoformat()}"

async def get_user_aura_data(user_id: int):
    """Fetches user data and calculates today's points earned."""
    user_doc = users_col.find_one({"_id": user_id})
    
    if not user_doc:
        user_doc = {
            "_id": user_id,
            "points": 0,
            "is_banned": False,
            "daily_point_log": {}
        }
        users_col.insert_one(user_doc)
    
    log_key = get_aura_point_log_key()
    points_today = user_doc.get("daily_point_log", {}).get(log_key, 0)
    
    return {
        "points": user_doc.get("points", 0),
        "is_banned": user_doc.get("is_banned", False),
        "points_today": points_today,
        "doc": user_doc
    }

async def grant_aura_points(user_id: int, points_to_add: int, reason: str = "") -> bool:
    """Grants points, respects the daily limit, and returns True if points were granted."""
    user_data = await get_user_aura_data(user_id)
    
    if user_data['is_banned']:
        return False

    points_today = user_data['points_today']
    log_key = get_aura_point_log_key()
    
    remaining_limit = AURA_POINT_LIMIT_DAILY - points_today
    
    if remaining_limit <= 0:
        return False

    points_actually_added = min(points_to_add, remaining_limit)
    
    if points_actually_added > 0:
        users_col.update_one(
            {"_id": user_id},
            {
                "$inc": {
                    "points": points_actually_added,
                    f"daily_point_log.{log_key}": points_actually_added
                }
            },
            upsert=True
        )
        return True
    return False

async def get_rank_for_user(user_id: int) -> int:
    """Calculates the user's rank based on total points."""
    # This uses a MongoDB aggregation pipeline to find the user's rank
    pipeline = [
        {"$sort": {"points": -1}},
        {"$group": {"_id": None, "users": {"$push": "$_id"}}}
    ]
    result = list(users_col.aggregate(pipeline))
    if not result:
        # If no users, rank is 1
        return 1 
    
    user_list = result[0]['users']
    try:
        rank = user_list.index(user_id) + 1
    except ValueError:
        # User exists but is not in the ranked list (e.g., points=0 and list is truncated)
        return users_col.count_documents({}) + 1 
        
    return rank


# -------------------------
# Bot Helper Functions (Keyboard Builders)
# -------------------------

def build_admin_keyboard(doc_id: str, doc_type: str, anon_id: int):
    """Builds a keyboard for admin moderation actions."""
    builder = InlineKeyboardBuilder()
    
    # doc_type is either 'conf' or 'comment'
    builder.add(InlineKeyboardButton(text="✅ Approve", callback_data=f"approve_{doc_type}_{doc_id}"))
    builder.add(InlineKeyboardButton(text="❌ Reject", callback_data=f"reject_{doc_type}_{doc_id}"))
    
    # Only show these for the main confession post, not for comments 
    if doc_type == 'conf':
        builder.add(InlineKeyboardButton(text="👁️ View Author", callback_data=f"author_conf_{doc_id}"))
        builder.add(InlineKeyboardButton(text="🛡️ Ban Author", callback_data=f"ban_anon_{anon_id}"))

    # For comments, allow banning the commenter's anonymous ID
    if doc_type == 'comment':
        builder.add(InlineKeyboardButton(text="🛡️ Ban Commenter", callback_data=f"ban_anon_{anon_id}"))
    
    builder.adjust(2)
    return builder.as_markup()

def build_comment_keyboard(conf_id: str):
    """Builds the public keyboard for adding a comment."""
    builder = InlineKeyboardBuilder()
    # The URL link allows users to start a conversation with the bot for that specific post
    builder.add(InlineKeyboardButton(text="💬 Add Comment", url=f"t.me/{BOT_USERNAME}?start=comment_{conf_id}"))
    builder.adjust(1)
    return builder.as_markup()

def build_full_view_keyboard(conf_id: str):
    """Builds the public keyboard for viewing the full post and comments in bot chat."""
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text="💬 View Full Post & Comments", url=f"t.me/{BOT_USERNAME}?start=comment_{conf_id}"))
    builder.adjust(1)
    return builder.as_markup()


# -------------------------
# Post-Approval Handlers (Aura Point Integration)
# -------------------------

async def after_confession_approved(conf_doc: dict):
    """Logic to execute after a confession is approved, including sending to channel and granting points."""
    bot = Bot.get_current()
    user_id = conf_doc['user_id']
    conf_id = str(conf_doc['_id'])
    conf_num = conf_doc['conf_num']

    # 1. Post to Channel
    text = f"**Confession #{conf_num}**\n\n{conf_doc['text']}"
    keyboard = build_full_view_keyboard(conf_id)
    
    try:
        if conf_doc.get('photo_file_id'):
            message = await bot.send_photo(
                CHANNEL_ID,
                photo=conf_doc['photo_file_id'],
                caption=text,
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
        else:
            message = await bot.send_message(
                CHANNEL_ID,
                text=text,
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
            
        # Store the channel message ID for comment linking
        conf_col.update_one(
            {"_id": ObjectId(conf_id)},
            {"$set": {"channel_message_id": message.message_id, "approved_at": datetime.now(UTC)}}
        )
        
        # 2. Grant Aura Points
        was_granted = await grant_aura_points(user_id, AURA_POINT_CONFESSION, "Confession approved")
        
        # 3. Notify the user
        message = f"✅ Your Confession **#{conf_num}** has been approved and posted to the channel! "
        if was_granted:
            message += f"You have been awarded **{AURA_POINT_CONFESSION} Aura Points**! (Daily limit: {AURA_POINT_LIMIT_DAILY} points)."
        else:
            message += f"You have reached your daily limit of {AURA_POINT_LIMIT_DAILY} Aura Points."
            
        await bot.send_message(user_id, message, parse_mode="Markdown")

    except TelegramForbiddenError:
        print(f"User {user_id} blocked the bot. Cannot send point notification.")
    except Exception as e:
        print(f"Error posting confession {conf_id} to channel or notifying user: {e}")

async def after_comment_approved(comment_doc: dict):
    """Logic to execute after a comment is approved, including granting aura points and updating channel message."""
    bot = Bot.get_current()
    user_id = comment_doc['user_id']
    conf_id = comment_doc['conf_id']
    comment_id = str(comment_doc['_id'])
    
    # 1. Grant Aura Points
    was_granted = await grant_aura_points(user_id, AURA_POINT_COMMENT, "Comment approved")
    
    # 2. Notify the user
    try:
        message = f"✅ Your comment (ID: `{comment_id[-6:]}`) on Confession ID `{conf_id[-6:]}` has been approved and posted! "
        if was_granted:
            message += f"You have been awarded **{AURA_POINT_COMMENT} Aura Points**! (Daily limit: {AURA_POINT_LIMIT_DAILY} points)."
        else:
            message += f"You have reached your daily limit of {AURA_POINT_LIMIT_DAILY} Aura Points."
            
        await bot.send_message(user_id, message, parse_mode="Markdown")
        
    except TelegramForbiddenError:
        print(f"User {user_id} blocked the bot. Cannot send point notification.")
    except Exception as e:
        print(f"Error sending comment approval message to {user_id}: {e}")

    # 3. Update the channel message comment counter (Optional: requires fetching conf doc)
    conf_doc = conf_col.find_one({"_id": ObjectId(conf_id), "approved": True})
    if conf_doc and conf_doc.get('channel_message_id'):
        comments_count = comments_col.count_documents({"conf_id": conf_id, "approved": True})
        
        # Telegram does not support editing the caption/text of a photo message without removing the photo.
        # So we only attempt to edit the reply markup if it's a text message or a specific scenario.
        # For simplicity, we just ensure the keyboard is still correct (which links to the full view).
        try:
            # We can't edit the text/caption to show the counter without re-sending media, so we rely on the keyboard.
            await bot.edit_message_reply_markup(
                chat_id=CHANNEL_ID,
                message_id=conf_doc['channel_message_id'],
                reply_markup=build_full_view_keyboard(conf_id)
            )
        except (TelegramBadRequest, TelegramAPIError) as e:
            # This is common if the message is too old or was deleted, can be ignored.
            print(f"Could not update channel message reply markup for {conf_id}: {e}")
            pass
        
# -------------------------
# Confession/Comment Display Logic
# -------------------------

async def show_confession_and_comments(msg: types.Message, conf_id: str):
    """
    Shows a single confession and its comments, used by /random, /latest, and /start comment_<id>
    """
    
    doc = conf_col.find_one({"_id": ObjectId(conf_id), "approved": True})
    if not doc:
        await msg.answer("Confession not found or not approved.")
        return

    # 1. Send Confession (could be photo or text)
    text = f"**Confession #{doc['conf_num']}**\n\n{doc['text']}"
    
    comments_count = comments_col.count_documents({"conf_id": conf_id, "approved": True})
    
    keyboard = build_comment_keyboard(conf_id)

    if doc.get('photo_file_id'):
        try:
            await msg.answer_photo(
                photo=doc['photo_file_id'],
                caption=text,
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
        except TelegramAPIError as e:
            # Fallback if photo ID is somehow invalid or expired
            await msg.answer(text=f"{text}\n\n**Note:** Photo could not be displayed: {e}", reply_markup=keyboard, parse_mode="Markdown")
    else:
        await msg.answer(
            text=text, 
            reply_markup=keyboard,
            parse_mode="Markdown"
        )


    # 2. Send Comments
    comments = list(comments_col.find({"conf_id": conf_id, "approved": True}).sort("created_at", 1))
    
    if not comments:
        await msg.answer("No comments yet. Be the first to reply!")
        return

    # Fetch Aura points for commenters to display next to their comment
    comment_data = []
    
    # Batch fetch user points to minimize DB calls
    user_ids = [c['user_id'] for c in comments]
    user_points_map = {
        doc['_id']: doc.get('points', 0) 
        for doc in users_col.find({"_id": {"$in": user_ids}}, {"points": 1})
    }
    
    for i, c in enumerate(comments):
        points = user_points_map.get(c['user_id'], 0)
        comment_data.append(f"**Comment {i+1}** (Aura: {points}): {c['text']}")

    
    MAX_LEN = 3500
    current_chunk = ""
    comment_messages = []
    
    # Chunk comments to avoid hitting the 4096 character limit
    for comment_line in comment_data:
        # Check if adding the next line + separators will exceed the max length
        if len(current_chunk) + len(comment_line) + 2 > MAX_LEN:
            comment_messages.append(current_chunk)
            current_chunk = ""
        current_chunk += comment_line + "\n\n"
    
    if current_chunk:
        comment_messages.append(current_chunk)
        
    await msg.answer(f"**-- {comments_count} Approved Comments --**", parse_mode="Markdown")

    for i, chunk in enumerate(comment_messages):
        await msg.answer(
            text=chunk,
            parse_mode="Markdown"
        )


# -------------------------
# Message Handlers (Confession/Comment Submission)
# -------------------------

@dp.message(F.text, Command("start", ignore_case=True))
@dp.message(F.text, Command("cancel", ignore_case=True))
@dp.message(F.text, Command("latest", ignore_case=True))
@dp.message(F.text, Command("random", ignore_case=True))
@dp.message(F.text, Command("aura", ignore_case=True))
@dp.message(F.text, Command("help", ignore_case=True))
async def handle_commands(msg: types.Message, state: FSMContext, command: CommandObject):
    """
    Interceptor to ensure that simple text messages that are actually commands 
    don't trigger the confession submission flow.
    """
    if msg.text.lower().startswith(("/start", "/cancel", "/latest", "/random", "/aura", "/help")):
        # The specific command handler will execute next.
        return
    
    # If it's just a random message not in a state, start the confession flow.
    if await state.get_state() is None:
        await cmd_confession(msg, state)


@dp.message(Command("start"))
async def cmd_start(msg: types.Message, state: FSMContext, command: CommandObject):
    await state.clear() 
    user_id = msg.from_user.id
    
    if await check_user_ban(user_id):
        await msg.answer("⛔️ **ACCESS DENIED:** You are banned from using this service.")
        return

    # Handle deep link for commenting: /start comment_<conf_id>
    if command.args and command.args.startswith("comment_"):
        conf_id = command.args.replace("comment_", "")
        if ObjectId.is_valid(conf_id):
            await state.set_state(CommentForm.waiting_for_text)
            await state.update_data(conf_id=conf_id)
            await msg.answer(
                f"You are now commenting on **Confession ID {conf_id[-6:]}**. "
                f"Please send your comment text now. Use /cancel to exit."
            )
            return

    await msg.answer(
        "👋 Welcome! Send me a message to post an anonymous confession. "
        "Use /latest to see recent posts, or /random for a blast from the past. "
        "Use /aura to check your points!\n\n"
        "**To Submit a Confession:** Just type and send your message now."
    )
    # Automatically jump to the confession state
    await state.set_state(ConfessionForm.waiting_for_text)


# --- Confession Submission FSM ---

@dp.message(F.text, ConfessionForm.waiting_for_text)
@dp.message(F.photo, ConfessionForm.waiting_for_text)
async def process_confession_submission(msg: types.Message, state: FSMContext):
    user_id = msg.from_user.id
    
    if await check_user_ban(user_id):
        await msg.answer("⛔️ **ACCESS DENIED:** You are banned from using this service.")
        await state.clear()
        return

    text = msg.caption if msg.photo else msg.text
    photo_file_id = msg.photo[-1].file_id if msg.photo else None

    if not text and not photo_file_id:
        await msg.answer("Please send the text for your confession (or text with a photo).")
        return

    # Check for empty or short text (if no photo)
    if not photo_file_id and (not text or len(text.strip()) < 10):
        await msg.answer("Your confession must contain at least 10 characters of text.")
        return
        
    await state.clear() # Clear state immediately upon submission

    anon_id = user_id # Using the actual user ID as the internal anonymous ID
    conf_num = get_next_confession_number()
    
    conf_doc = {
        "user_id": user_id,
        "anon_id": anon_id, # Internal identifier for tracking/banning
        "conf_num": conf_num,
        "text": text,
        "photo_file_id": photo_file_id,
        "submitted_at": datetime.now(UTC),
        "approved": False,
        "channel_message_id": None
    }
    
    # Insert into database
    result = conf_col.insert_one(conf_doc)
    conf_id = str(result.inserted_id)
    
    # 1. Notify Admin for Confession Approval
    admin_keyboard = build_admin_keyboard(conf_id, 'conf', anon_id)
    admin_message_text = (
        f"🚨 **NEW CONFESSION FOR MODERATION**\n\n"
        f"Confession ID: `{conf_id}`\n"
        f"Anon ID: `{anon_id}`\n"
        f"Text: {text}"
    )

    try:
        if photo_file_id:
            await bot.send_photo(
                LOG_CHAT_ID,
                photo=photo_file_id,
                caption=admin_message_text,
                reply_markup=admin_keyboard,
                parse_mode="Markdown"
            )
        else:
            await bot.send_message(
                LOG_CHAT_ID,
                text=admin_message_text,
                reply_markup=admin_keyboard,
                parse_mode="Markdown"
            )
            
        # 2. Confirm Submission to User
        await msg.answer(
            "✅ Confession submitted! It is now pending review by an admin. You will be notified if it is approved."
        )
        
    except TelegramAPIError as e:
        await msg.answer(
            "❌ Submission failed. An error occurred when notifying the admin. Please try again later."
        )
        print(f"Error sending confession to log chat: {e}")
        conf_col.delete_one({"_id": ObjectId(conf_id)}) # Clean up failed submission


# --- Comment Submission FSM ---

@dp.message(F.text, CommentForm.waiting_for_text)
async def process_comment_submission(msg: types.Message, state: FSMContext):
    user_id = msg.from_user.id
    
    if await check_user_ban(user_id):
        await msg.answer("⛔️ **ACCESS DENIED:** You are banned from using this service.")
        await state.clear()
        return

    data = await state.get_data()
    conf_id = data.get('conf_id')
    comment_text = msg.text
    
    await state.clear() # Clear state immediately upon submission

    if not conf_id:
        await msg.answer("❌ Error: Confession ID missing. Please start over using the 'Add Comment' button.")
        return

    if not comment_text or len(comment_text.strip()) < 5:
        await msg.answer("❌ Your comment must contain at least 5 characters of text.")
        return
        
    anon_id = user_id # Using the actual user ID as the internal anonymous ID
    
    comment_doc = {
        "user_id": user_id,
        "anon_id": anon_id, # Internal identifier for tracking/banning
        "conf_id": conf_id,
        "text": comment_text,
        "submitted_at": datetime.now(UTC),
        "approved": False
    }
    
    # Insert into database
    result = comments_col.insert_one(comment_doc)
    comment_db_id = str(result.inserted_id)
    
    # 1. Notify Admin for Comment Approval
    admin_keyboard = build_admin_keyboard(comment_db_id, 'comment', anon_id)
    
    # Fetch the original confession number for context
    conf_doc = conf_col.find_one({"_id": ObjectId(conf_id)})
    conf_num = conf_doc.get('conf_num', 'N/A') if conf_doc else 'N/A'
    
    admin_message_text = (
        f"💬 **NEW COMMENT FOR MODERATION**\n\n"
        f"Confession #: {conf_num}\n"
        f"Comment ID: `{comment_db_id}`\n"
        f"Anon ID: `{anon_id}`\n"
        f"Comment: {comment_text}"
    )

    try:
        await bot.send_message(
            LOG_CHAT_ID,
            text=admin_message_text,
            reply_markup=admin_keyboard,
            parse_mode="Markdown"
        )
            
        # 2. Confirm Submission to User
        await msg.answer(
            "✅ Comment submitted! It is now pending review by an admin. You will be notified if it is approved."
        )
        
    except TelegramAPIError as e:
        await msg.answer(
            "❌ Submission failed. An error occurred when notifying the admin. Please try again later."
        )
        print(f"Error sending comment to log chat: {e}")
        comments_col.delete_one({"_id": ObjectId(comment_db_id)}) # Clean up failed submission


# -------------------------
# Admin Callback Handlers
# -------------------------

@dp.callback_query(F.data.startswith(("approve_", "reject_")))
async def admin_moderate_post(callback_query: types.CallbackQuery):
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("🚫 Unauthorized action.", show_alert=True)
        return
        
    action, doc_type, doc_id = callback_query.data.split('_')
    
    is_confession = (doc_type == 'conf')
    collection = conf_col if is_confession else comments_col
    
    doc = collection.find_one({"_id": ObjectId(doc_id)})
    if not doc:
        await callback_query.answer("❌ Post not found or already handled.", show_alert=True)
        return

    # Check if already approved/rejected
    if doc.get('approved') is not None:
        if doc.get('approved') and action == 'approve':
            await callback_query.answer("This post is already approved.", show_alert=True)
            return
        if not doc.get('approved') and action == 'reject':
            await callback_query.answer("This post is already rejected/deleted.", show_alert=True)
            return
            
    try:
        if action == 'approve':
            # Mark as approved and run post-approval logic
            collection.update_one({"_id": ObjectId(doc_id)}, {"$set": {"approved": True, "moderated_by": callback_query.from_user.id}})
            
            if is_confession:
                await after_confession_approved(doc)
                log_message = f"Confession #{doc.get('conf_num', doc_id)} has been **APPROVED**."
            else:
                await after_comment_approved(doc)
                log_message = f"Comment ID {doc_id[-6:]} has been **APPROVED**."

            # Edit the admin message to reflect the action
            await callback_query.message.edit_reply_markup(reply_markup=None) # Remove buttons
            await callback_query.message.edit_caption(
                caption=callback_query.message.caption + f"\n\n**✅ APPROVED** by Admin {callback_query.from_user.id}",
                parse_mode="Markdown"
            )
            
        elif action == 'reject':
            # Mark as rejected and notify user
            collection.delete_one({"_id": ObjectId(doc_id)})
            
            user_id = doc['user_id']
            try:
                await bot.send_message(user_id, "❌ Your submission was reviewed and rejected by an admin.")
            except TelegramForbiddenError:
                pass # User blocked bot
                
            if is_confession:
                log_message = f"Confession #{doc.get('conf_num', doc_id)} has been **REJECTED** and deleted."
            else:
                log_message = f"Comment ID {doc_id[-6:]} has been **REJECTED** and deleted."
                
            # Edit the admin message to reflect the action
            await callback_query.message.edit_reply_markup(reply_markup=None) # Remove buttons
            await callback_query.message.edit_caption(
                caption=callback_query.message.caption + f"\n\n**❌ REJECTED** by Admin {callback_query.from_user.id}",
                parse_mode="Markdown"
            )

        await callback_query.answer(log_message, show_alert=False)

    except Exception as e:
        await callback_query.answer(f"An error occurred during moderation: {e}", show_alert=True)
        print(f"Error during moderation: {e}")


@dp.callback_query(F.data.startswith("author_"))
async def admin_view_author(callback_query: types.CallbackQuery):
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("🚫 Unauthorized action.", show_alert=True)
        return
        
    _, doc_type, doc_id = callback_query.data.split('_')
    
    collection = conf_col if doc_type == 'conf' else comments_col
    doc = collection.find_one({"_id": ObjectId(doc_id)})
    
    if not doc:
        await callback_query.answer("❌ Post not found.", show_alert=True)
        return
        
    user_id = doc['user_id']
    
    try:
        # Fetch actual user details (optional, but shows bot is connected to the user)
        user_info = await bot.get_chat(user_id)
        
        message_text = (
            f"**Author Details (Conf. ID: {doc_id[-6:]})**\n"
            f"**Telegram ID:** `{user_id}`\n"
            f"**Username:** @{user_info.username if user_info.username else 'N/A'}\n"
            f"**Full Name:** {user_info.full_name}\n"
            f"**Anon ID:** `{doc['anon_id']}` (Use this for banning)"
        )
    except TelegramNotFound:
        message_text = (
            f"**Author Details (Conf. ID: {doc_id[-6:]})**\n"
            f"**Telegram ID:** `{user_id}`\n"
            f"**User Info:** (User not found/deleted profile)\n"
            f"**Anon ID:** `{doc['anon_id']}` (Use this for banning)"
        )
    
    await callback_query.message.answer(message_text, parse_mode="Markdown")
    await callback_query.answer("Author details sent.")


@dp.callback_query(F.data.startswith("ban_anon_"))
async def admin_prepare_ban(callback_query: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("🚫 Unauthorized action.", show_alert=True)
        return
        
    anon_id_str = callback_query.data.split('_')[-1]
    
    try:
        anon_id = int(anon_id_str)
    except ValueError:
        await callback_query.answer("Invalid Anon ID format.", show_alert=True)
        return

    await state.set_state(AdminForm.waiting_for_reason)
    await state.update_data(ban_anon_id=anon_id, admin_message_id=callback_query.message.message_id)
    
    await callback_query.message.answer(
        f"🔒 **CONFIRM BAN**\n"
        f"Please send the reason for banning Anon ID `{anon_id}`. Use /cancel to abort."
    )
    await callback_query.answer()


@dp.message(F.text, AdminForm.waiting_for_reason)
async def admin_execute_ban(msg: types.Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        return
        
    data = await state.get_data()
    anon_id = data.get('ban_anon_id')
    admin_message_id = data.get('admin_message_id')
    ban_reason = msg.text
    
    await state.clear()
    
    if not anon_id:
        await msg.answer("❌ Ban operation failed: Anon ID missing.")
        return

    # 1. Update the user collection (primary ban flag)
    users_col.update_one(
        {"_id": anon_id},
        {"$set": {"is_banned": True}},
        upsert=True
    )
    
    # 2. Log the ban in the dedicated bans collection
    bans_col.update_one(
        {"_id": anon_id},
        {"$set": {
            "banned_by": msg.from_user.id,
            "ban_reason": ban_reason,
            "banned_at": datetime.now(UTC)
        }},
        upsert=True
    )

    # 3. Notify the user (if possible)
    try:
        await bot.send_message(
            anon_id, 
            f"⛔️ **ACTION REQUIRED:** You have been permanently banned from submitting content or earning Aura Points due to moderation issues.\n\n"
            f"**Reason:** {ban_reason}"
        )
    except TelegramForbiddenError:
        print(f"Banned user {anon_id} blocked the bot.")
    except Exception as e:
        print(f"Error notifying banned user {anon_id}: {e}")

    # 4. Notify admin (in the current chat)
    await msg.answer(
        f"✅ Anon ID `{anon_id}` has been **PERMANENTLY BANNED**.\n"
        f"Reason: {ban_reason}",
        parse_mode="Markdown"
    )
    
    # 5. Update the original admin message to show the ban
    try:
        if admin_message_id:
            await bot.edit_message_caption(
                chat_id=LOG_CHAT_ID,
                message_id=admin_message_id,
                caption=(msg.caption if msg.caption else msg.text) + f"\n\n**🔒 AUTHOR BANNED**",
                reply_markup=None # Remove old buttons
            )
    except Exception as e:
        print(f"Could not update original admin message after ban: {e}")


# -------------------------
# Bot Functions (Commands)
# -------------------------

@dp.message(Command("aura"))
async def cmd_aura(msg: types.Message):
    """Shows the user's current aura points and rank."""
    user_id = msg.from_user.id
    
    user_data = await get_user_aura_data(user_id)
    points = user_data['points']
    points_today = user_data['points_today']
    is_banned = user_data['is_banned']
    
    if is_banned:
        await msg.answer("❌ You are currently banned and cannot earn or check your Aura points.")
        return

    rank = await get_rank_for_user(user_id)
    
    # Calculate time until daily limit resets (midnight UTC+3)
    now = get_current_time_utc_plus(TIME_ZONE_OFFSET)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    time_until_reset = tomorrow - now
    
    hours, remainder = divmod(time_until_reset.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    
    reset_time_str = f"{hours}h {minutes}m"
    
    response_text = (
        f"✨ **Your Aura Status** ✨\n\n"
        f"**Total Aura Points:** `{points}`\n"
        f"**Current Rank:** #{rank}\n\n"
        f"**Points Earned Today:** `{points_today}` / `{AURA_POINT_LIMIT_DAILY}`\n"
        f"**Reset in:** {reset_time_str}\n\n"
        f"_Aura points are earned by submitting approved content (confessions and comments). Use /latest or /random to find posts to comment on!_"
    )
    
    await msg.answer(response_text, parse_mode="Markdown")

@dp.message(Command("latest"))
async def cmd_latest(msg: types.Message):
    user_id = msg.from_user.id
    if await check_user_ban(user_id):
        await msg.answer("⛔️ **ACCESS DENIED:** You are banned from viewing content.")
        return
        
    docs = list(conf_col.find({"approved": True}).sort("approved_at", -1).limit(5))
    if not docs:
        await msg.answer("No approved confessions yet.")
        return
        
    await msg.answer(f"🔎 Showing latest {len(docs)} confessions. Use the /start comment_<id> link on any post in the channel to see its full view.")
    
    for doc in docs:
        await show_confession_and_comments(msg, str(doc["_id"]))


@dp.message(Command("random"))
async def cmd_random(msg: types.Message):
    user_id = msg.from_user.id
    if await check_user_ban(user_id):
        await msg.answer("⛔️ **ACCESS DENIED:** You are banned from viewing content.")
        return

    count = conf_col.count_documents({"approved": True})
    if count == 0:
        await msg.answer("No approved confessions yet.")
        return
    
    # Select a random document
    skip = random.randint(0, max(0, count-1))
    docs = list(conf_col.find({"approved": True}).skip(skip).limit(1))
    
    if not docs:
        await msg.answer("No results.")
        return
    
    await show_confession_and_comments(msg, str(docs[0]["_id"]))

@dp.message(Command("cancel"))
async def cmd_cancel(msg: types.Message, state: FSMContext):
    """Allows user to cancel any ongoing state."""
    current_state = await state.get_state()
    if current_state is None:
        await msg.answer("Nothing to cancel.")
        return

    await state.clear()
    await msg.answer("Operation cancelled. You can now start a new action or submit a confession.")

@dp.message(Command("admin_status"))
async def cmd_admin_status(msg: types.Message):
    """Admin command to check bot status."""
    if not is_admin(msg.from_user.id):
        return

    conf_count = conf_col.count_documents({})
    approved_conf_count = conf_col.count_documents({"approved": True})
    pending_conf_count = conf_col.count_documents({"approved": False})
    
    comment_count = comments_col.count_documents({})
    approved_comment_count = comments_col.count_documents({"approved": True})
    pending_comment_count = comments_col.count_documents({"approved": False})
    
    last_conf_num = get_last_confession_number()

    status_text = (
        f"**🤖 BOT STATUS**\n"
        f"**--- Confessions ---**\n"
        f"Total Confessions: `{conf_count}`\n"
        f"Approved: `{approved_conf_count}`\n"
        f"**Pending:** `{pending_conf_count}`\n"
        f"Last Confession #: `{last_conf_num}`\n"
        f"\n**--- Comments ---**\n"
        f"Total Comments: `{comment_count}`\n"
        f"Approved: `{approved_comment_count}`\n"
        f"**Pending:** `{pending_comment_count}`\n"
        f"\n**--- System ---**\n"
        f"Channel ID: `{CHANNEL_ID}`\n"
        f"Log Chat ID: `{LOG_CHAT_ID}`\n"
        f"Timezone Offset: `{TIME_ZONE_OFFSET.total_seconds() / 3600} hours`"
    )
    await msg.answer(status_text, parse_mode="Markdown")

# -------------------------
# Keep-Alive Web Server for Hosting
# -------------------------
from aiohttp import web

async def handle(request):
    """Simple handler to respond to health checks."""
    return web.Response(text="Confession bot is running...")

async def start_web_server():
    """Starts a simple web server if the PORT environment variable is set."""
    # This is critical for hosting platforms like Render to keep the bot alive
    port = os.getenv("PORT")
    if port: 
        app = web.Application()
        app.router.add_get("/", handle)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", int(port))
        print(f"Starting web server on port {port}")
        await site.start()
    else:
        print("PORT environment variable not set. Not starting web server.")


# -------------------------
# Main entry point
# -------------------------
bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher()

# Register all handlers (most message and callback handlers are registered via decorators)

async def main():
    print("Bot starting... Registering handlers...")
    
    # Handlers are registered via decorators. Start the loop.
    await asyncio.gather(
        start_web_server(), # Start the web server first
        dp.start_polling(bot) # Start polling the Telegram API
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot stopped by user.")
    except Exception as e:
        print(f"An error occurred in main: {e}")
